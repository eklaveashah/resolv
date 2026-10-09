"""Data contracts for the Resolv pricing engine.

Every request/response in the engine is one of these dataclasses, so the
math core stays pure (no HTTP, no I/O) and fully unit-testable.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Market:
    """A binary contract as the engine sees it.

    kind:
      "event"     — no tradable underlying (Fed decision, election, bill passes).
      "threshold" — underlying crosses a strike by expiry (BTC > $120K on date).
    price_yes: current market mid for the YES side, in probability space (0..1).
    """
    id: str
    title: str
    kind: str                      # "event" | "threshold"
    price_yes: float               # market mid, 0..1
    venue: str = ""
    liquidity_score: float = 0.5   # 0..1 (depth/volume normalized upstream)
    days_to_expiry: float = 30.0
    fee_rate: float = 0.02         # round-trip fees as prob-space cost
    # threshold-only fields:
    spot: Optional[float] = None
    strike: Optional[float] = None
    annual_vol: Optional[float] = None   # e.g. 0.55 = 55% annualized
    drift: float = 0.0                   # annual drift of ln(S); 0 = martingale
    rate: float = 0.0                    # discount rate; ~0 for short horizons


@dataclass
class Evidence:
    """One piece of qualitative evidence (headline, filing, poll, note).

    The engine turns this into a likelihood ratio — see evidence.py.
    sentiment: -1..1 (supply it, or let sentiment.py score raw text).
    relevance: 0..1, how directly this bears on the contract question.
    source_weight: 0..1 credibility prior for the source.
    age_hours: recency; older evidence decays.
    """
    text: str
    sentiment: Optional[float] = None
    relevance: float = 1.0
    source_weight: float = 0.6
    age_hours: float = 6.0
    source: str = ""


@dataclass
class QualRubric:
    """Structured qualitative judgments an analyst supplies.

    Each field is a named scale with documented numeric effect (qual -> quant):
      resolution_clarity: "clear" | "moderate" | "vague"
          -> ambiguity a in {0.08, 0.22, 0.40}; shrinks fair value toward 0.5
             and caps confidence.
      base_rate: optional analyst/base-rate probability for the event class.
      base_rate_weight: 0..1 blend weight for that anchor (in log-odds space).
      insider_signal: "none" | "weak" | "strong" bullish/bearish is expressed
          by sign of insider_direction (+1/-1); adds a bounded log-odds term.
    """
    resolution_clarity: str = "moderate"
    base_rate: Optional[float] = None
    base_rate_weight: float = 0.25
    insider_signal: str = "none"     # "none" | "weak" | "strong"
    insider_direction: int = 0       # +1 bullish YES, -1 bearish


@dataclass
class Contribution:
    """One additive log-odds term in the pricing pipeline."""
    name: str
    delta_log_odds: float
    prob_before: float
    prob_after: float
    note: str = ""


@dataclass
class PriceResult:
    market_id: str
    market_prob: float          # raw market mid
    prior_prob: float           # after de-bias (longshot correction)
    fair_prob: float            # engine output
    edge: float                 # fair - market
    confidence: float           # 0..1
    verdict: str                # BUY_YES | BUY_NO | HOLD
    hold_reasons: List[str] = field(default_factory=list)
    contributions: List[Contribution] = field(default_factory=list)
    kelly_full: float = 0.0
    kelly_suggested: float = 0.0   # half-Kelly x confidence, capped
    ev_after_fees: float = 0.0     # per $1 stake on the recommended side
    model_note: str = ""
