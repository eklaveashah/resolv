"""Resolv pricing core — the "brain".

Everything happens in LOG-ODDS space, which buys two properties:
  1. every factor is one additive term, so attribution is exact
     (prior + sum(contributions) == posterior, always), and
  2. bounded factor caps translate to bounded probability moves near 0.5
     and progressively smaller moves near the extremes, which is the
     behaviour a probability forecast should have.

Pipeline (documented per-step in price()):

  market mid
    -> [1] longshot de-bias           (calibration-curve prior)
    -> [2] structural model           (threshold markets: digital-option N(d2)
                                       blended in log-odds; event markets: none)
    -> [3] evidence engine            (sentiment -> likelihood ratios, capped)
    -> [4] qualitative rubric         (base-rate anchor, insider term)
    -> [5] ambiguity shrink           (toward 0.5, from resolution clarity)
    -> fair probability, confidence, verdict, Kelly sizing

References (model motivation, not implementations):
  - digital/binary option value = e^{-rT} * N(d2)  (Black-Scholes machinery)
  - favorite-longshot bias correction via logit-linear calibration
    (Page & Clemen 2013 style: logit(p') = a + b*logit(p), b > 1)
  - Bayesian updating with capped log-likelihood ratios.

This module is dependency-free and deterministic: same inputs -> same output.
"""
import math
from typing import List, Optional, Tuple

from .models import Contribution, Evidence, Market, PriceResult, QualRubric
from . import sentiment as senti

# ---------------- tunables (documented defaults) ----------------
CAL_A = 0.0            # calibration intercept
CAL_B = 1.06           # calibration slope; >1 corrects favorite-longshot bias
MODEL_BLEND_W = 0.35   # log-odds weight of structural model vs market prior
EVIDENCE_K = 0.35      # max |log-odds| per single max-strength evidence item
EVIDENCE_CAP = 1.00    # cap on total evidence log-odds shift (~±12pts at 50%)
RECENCY_HALF_LIFE_H = 36.0
INSIDER_LO = {"none": 0.0, "weak": 0.15, "strong": 0.40}
AMBIGUITY = {"clear": 0.08, "moderate": 0.22, "vague": 0.40}
AMBIGUITY_SHRINK = 0.55   # fraction of ambiguity applied as shrink toward 0.5
EDGE_GATE = 0.04          # min |edge| to act
CONF_GATE = 0.50          # min confidence to act
KELLY_CAP = 0.15          # never suggest > 15% of bankroll
P_FLOOR, P_CEIL = 0.005, 0.995


# ---------------- math primitives ----------------
def norm_cdf(x: float) -> float:
    """Standard normal CDF via erf (double precision, stdlib only)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def clamp_p(p: float) -> float:
    return max(P_FLOOR, min(P_CEIL, p))


def logit(p: float) -> float:
    p = clamp_p(p)
    return math.log(p / (1.0 - p))


def inv_logit(lo: float) -> float:
    if lo > 40:
        return P_CEIL
    if lo < -40:
        return P_FLOOR
    return clamp_p(1.0 / (1.0 + math.exp(-lo)))


def realized_vol(prices: List[float], periods_per_year: float = 365.0) -> float:
    """Annualized realized volatility from a price series (close-to-close)."""
    if len(prices) < 3:
        raise ValueError("need >= 3 prices for realized vol")
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))
            if prices[i] > 0 and prices[i - 1] > 0]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var * periods_per_year)


# ---------------- [2] structural model: digital option ----------------
def digital_option_prob(spot: float, strike: float, annual_vol: float,
                        days: float, drift: float = 0.0) -> float:
    """P(S_T > K) under geometric Brownian motion — the N(d2) of Black-Scholes.

        d2 = [ln(S/K) + (mu - sigma^2/2) * T] / (sigma * sqrt(T))

    With drift mu = 0 (martingale default) this is a pure vol-and-distance
    probability. The undiscounted N(d2) IS the fair probability of a binary
    contract; discounting (e^{-rT}) matters for cash value, not probability,
    and prediction-market horizons are short — callers can discount output
    with discount_factor() if they want cash fair value.
    """
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if annual_vol <= 0:
        raise ValueError("annual_vol must be positive")
    if days <= 0:
        # expired: probability collapses to indicator
        return 1.0 if spot > strike else 0.0
    t = days / 365.0
    d2 = (math.log(spot / strike) + (drift - 0.5 * annual_vol ** 2) * t) \
        / (annual_vol * math.sqrt(t))
    return clamp_p(norm_cdf(d2))


def discount_factor(rate: float, days: float) -> float:
    return math.exp(-rate * days / 365.0)


# ---------------- [1] longshot de-bias ----------------
def debias_market_prob(p_market: float, a: float = CAL_A, b: float = CAL_B) -> float:
    """logit-linear calibration: logit(p') = a + b*logit(p).

    b > 1 stretches the logit, i.e. longshots (overpriced by crowds) get
    marked DOWN and favorites (underpriced) get marked UP — the documented
    favorite-longshot bias in prediction markets.
    """
    return inv_logit(a + b * logit(p_market))


# ---------------- [3] evidence engine (qual -> quant) ----------------
def evidence_log_odds(items: List[Evidence]) -> Tuple[float, List[Tuple[Evidence, float, float]]]:
    """Map each evidence item to a capped log-likelihood-ratio term.

        lr_i = K * sentiment_i * relevance_i * source_weight_i * decay_i
        decay_i = 0.5 ** (age_hours / HALF_LIFE)

    Items whose sentiment is None are scored by the lexicon first, so raw
    headlines can be posted directly. The SUM is capped at ±EVIDENCE_CAP so
    twenty copies of the same story cannot move the price without bound.
    Returns (total_capped, [(item, sentiment_used, lr_i), ...]).
    """
    detail = []
    total = 0.0
    for ev in items:
        s = ev.sentiment
        if s is None:
            s, _ = senti.score(ev.text)
        s = max(-1.0, min(1.0, s))
        decay = 0.5 ** (max(0.0, ev.age_hours) / RECENCY_HALF_LIFE_H)
        rel = max(0.0, min(1.0, ev.relevance))
        sw = max(0.0, min(1.0, ev.source_weight))
        lr = EVIDENCE_K * s * rel * sw * decay
        detail.append((ev, s, lr))
        total += lr
    capped = max(-EVIDENCE_CAP, min(EVIDENCE_CAP, total))
    return capped, detail


# ---------------- Kelly ----------------
def kelly_yes(p: float, price_yes: float) -> float:
    """Full Kelly for buying YES at price m with win prob p.

    Binary payout: risk m to win (1-m).  b = (1-m)/m
    f* = (b*p - (1-p)) / b, floored at 0.
    """
    m = clamp_p(price_yes)
    b = (1.0 - m) / m
    f = (b * p - (1.0 - p)) / b
    return max(0.0, f)


def kelly_no(p: float, price_yes: float) -> float:
    """Full Kelly for buying NO at price (1-m) with win prob (1-p)."""
    return kelly_yes(1.0 - p, 1.0 - clamp_p(price_yes))


# ---------------- the pipeline ----------------
def price(market: Market,
          evidence: Optional[List[Evidence]] = None,
          rubric: Optional[QualRubric] = None) -> PriceResult:
    """Run the full Resolv pricing pipeline for one market."""
    evidence = evidence or []
    rubric = rubric or QualRubric()
    contribs: List[Contribution] = []

    if not (0.0 < market.price_yes < 1.0):
        raise ValueError("price_yes must be strictly between 0 and 1")

    # ---- [1] prior: de-biased market price
    p_mkt = clamp_p(market.price_yes)
    p_prior = debias_market_prob(p_mkt)
    lo = logit(p_prior)
    contribs.append(Contribution(
        name="longshot_debias",
        delta_log_odds=logit(p_prior) - logit(p_mkt),
        prob_before=p_mkt, prob_after=p_prior,
        note=f"logit-linear calibration (b={CAL_B}) corrects favorite-longshot bias"))

    # ---- [2] structural model (threshold markets only)
    model_note = "event market: Bayesian log-odds engine (no tradable underlying)"
    if market.kind == "threshold":
        if market.spot is None or market.strike is None or market.annual_vol is None:
            raise ValueError("threshold market needs spot, strike, annual_vol")
        p_model = digital_option_prob(market.spot, market.strike,
                                      market.annual_vol, market.days_to_expiry,
                                      market.drift)
        before = inv_logit(lo)
        lo = (1.0 - MODEL_BLEND_W) * lo + MODEL_BLEND_W * logit(p_model)
        contribs.append(Contribution(
            name="digital_option_N(d2)",
            delta_log_odds=lo - logit(before),
            prob_before=before, prob_after=inv_logit(lo),
            note=(f"BS digital: N(d2)={p_model:.3f} from spot={market.spot:g}, "
                  f"strike={market.strike:g}, vol={market.annual_vol:.0%}, "
                  f"T={market.days_to_expiry:.0f}d; blended w={MODEL_BLEND_W}")))
        model_note = "threshold market: Black-Scholes digital N(d2) blended with market prior"

    # ---- [3] evidence (sentiment -> likelihood ratios)
    ev_lo, ev_detail = evidence_log_odds(evidence)
    if evidence:
        before = inv_logit(lo)
        lo += ev_lo
        top = sorted(ev_detail, key=lambda d: -abs(d[2]))[:3]
        contribs.append(Contribution(
            name="news_evidence",
            delta_log_odds=ev_lo,
            prob_before=before, prob_after=inv_logit(lo),
            note=(f"{len(evidence)} item(s), capped log-odds {ev_lo:+.3f}; top: "
                  + "; ".join(f"'{d[0].text[:40]}' s={d[1]:+.2f} lr={d[2]:+.3f}"
                              for d in top))))

    # ---- [4] qualitative rubric
    if rubric.base_rate is not None:
        w = max(0.0, min(1.0, rubric.base_rate_weight))
        before = inv_logit(lo)
        lo = (1.0 - w) * lo + w * logit(rubric.base_rate)
        contribs.append(Contribution(
            name="base_rate_anchor",
            delta_log_odds=lo - logit(before),
            prob_before=before, prob_after=inv_logit(lo),
            note=f"analyst base rate {rubric.base_rate:.0%} blended w={w}"))
    ins = INSIDER_LO.get(rubric.insider_signal, 0.0) * (1 if rubric.insider_direction >= 0 else -1)
    if ins != 0.0 and rubric.insider_direction != 0:
        before = inv_logit(lo)
        lo += ins
        contribs.append(Contribution(
            name="qualitative_signal",
            delta_log_odds=ins,
            prob_before=before, prob_after=inv_logit(lo),
            note=f"rubric '{rubric.insider_signal}' signal, direction {rubric.insider_direction:+d}"))

    # ---- [5] ambiguity shrink toward 0.5
    amb = AMBIGUITY.get(rubric.resolution_clarity, AMBIGUITY["moderate"])
    before = inv_logit(lo)
    lo *= (1.0 - AMBIGUITY_SHRINK * amb)
    contribs.append(Contribution(
        name="ambiguity_shrink",
        delta_log_odds=lo - logit(before),
        prob_before=before, prob_after=inv_logit(lo),
        note=f"resolution clarity '{rubric.resolution_clarity}' -> ambiguity {amb:.0%}, shrink toward 50%"))

    p_fair = inv_logit(lo)
    edge = p_fair - p_mkt

    # ---- confidence
    n_ev = len(evidence)
    agree = 0.0
    if n_ev:
        signs = [1 if d[2] > 0 else -1 if d[2] < 0 else 0 for d in ev_detail]
        agree = abs(sum(signs)) / max(1, len(signs))
    confidence = 0.30 \
        + 0.25 * max(0.0, min(1.0, market.liquidity_score)) \
        + 0.10 * min(n_ev, 4) / 4.0 \
        + 0.10 * agree \
        - 0.45 * amb
    if market.kind == "threshold":
        confidence += 0.10   # structural model adds information
    confidence = max(0.10, min(0.90, confidence))

    # ---- verdict gates
    hold_reasons = []
    if abs(edge) < EDGE_GATE:
        hold_reasons.append(f"edge {edge * 100:+.1f}pts below {EDGE_GATE * 100:.0f}pt gate")
    if confidence < CONF_GATE:
        hold_reasons.append(f"confidence {confidence:.0%} below {CONF_GATE:.0%} gate")
    if not evidence and market.kind == "event":
        hold_reasons.append("no evidence supplied; estimate anchored to market")
    verdict = "HOLD"
    if not hold_reasons:
        verdict = "BUY_YES" if edge > 0 else "BUY_NO"

    # ---- sizing + EV (after fees) on the recommended side
    if verdict == "BUY_YES" or (verdict == "HOLD" and edge >= 0):
        kf = kelly_yes(p_fair, p_mkt)
        ev_net = p_fair * (1 - p_mkt) - (1 - p_fair) * p_mkt - market.fee_rate
    else:
        kf = kelly_no(p_fair, p_mkt)
        ev_net = (1 - p_fair) * p_mkt - p_fair * (1 - p_mkt) - market.fee_rate
    k_sug = min(KELLY_CAP, 0.5 * kf * confidence)
    if verdict == "HOLD":
        k_sug = 0.0

    return PriceResult(
        market_id=market.id,
        market_prob=p_mkt,
        prior_prob=p_prior,
        fair_prob=p_fair,
        edge=edge,
        confidence=confidence,
        verdict=verdict,
        hold_reasons=hold_reasons,
        contributions=contribs,
        kelly_full=kf,
        kelly_suggested=k_sug,
        ev_after_fees=ev_net,
        model_note=model_note,
    )
