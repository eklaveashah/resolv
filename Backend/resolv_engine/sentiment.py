"""Lexicon sentiment scorer.

Deliberately transparent: a weighted keyword lexicon over lowercased text,
squashed to [-1, 1]. In production this module is the seam where a real NLP
service (or an LLM scorer with logged prompts) plugs in — the rest of the
engine only sees a number in [-1, 1], so the swap is contained here.
"""
import math
import re
from typing import Dict, Tuple

_POS: Dict[str, float] = {
    "beat": 1.0, "beats": 1.0, "surge": 1.2, "surges": 1.2, "rally": 1.0,
    "rallies": 1.0, "record": 0.8, "inflow": 1.0, "inflows": 1.0,
    "approve": 1.0, "approved": 1.2, "passes": 1.2, "pass": 0.8,
    "cool": 0.8, "cools": 0.8, "cooling": 0.8, "soft": 0.6, "softer": 0.8,
    "cut": 0.8, "cuts": 0.8, "ease": 0.8, "eases": 0.8, "easing": 0.8,
    "gain": 0.8, "gains": 0.8, "climb": 0.8, "climbs": 0.8, "rebound": 0.9,
    "optimism": 0.9, "strong": 0.7, "strength": 0.7, "tops": 0.9,
    "upgrade": 1.0, "bullish": 1.2, "wins": 1.0, "win": 0.8, "clears": 1.0,
    "cleared": 1.0, "advance": 0.8, "advances": 0.8, "signs": 0.6,
}
_NEG: Dict[str, float] = {
    "miss": -1.0, "misses": -1.0, "fall": -0.8, "falls": -0.8, "drop": -0.8,
    "drops": -0.8, "slump": -1.1, "plunge": -1.3, "plunges": -1.3,
    "hawkish": -1.0, "hot": -0.6, "hotter": -0.9, "sticky": -0.8,
    "delay": -1.0, "delays": -1.0, "delayed": -1.0, "stall": -1.0,
    "stalls": -1.0, "stalled": -1.0, "fears": -0.9, "fear": -0.9,
    "risk": -0.5, "risks": -0.5, "recession": -0.8, "lawsuit": -0.9,
    "probe": -0.9, "dissent": -0.9, "veto": -1.1, "blocks": -1.0,
    "blocked": -1.0, "rejects": -1.2, "rejected": -1.2, "fails": -1.2,
    "downgrade": -1.0, "bearish": -1.2, "warns": -0.8, "warning": -0.8,
    "shutdown": -0.7, "crash": -1.4, "collapse": -1.3, "weak": -0.7,
    "pressure": -0.6, "concern": -0.6, "concerns": -0.6,
}
_NEGATORS = re.compile(r"\b(no|not|never|without|unlikely|fails to|won't|wont)\s+(\w+)")


def score(text: str) -> Tuple[float, Dict[str, float]]:
    """Return (sentiment in [-1,1], {matched_word: weight}).

    tanh squash keeps single strong words from saturating the scale and
    makes multi-signal headlines score higher than one-word matches.
    """
    t = (text or "").lower()
    hits: Dict[str, float] = {}
    total = 0.0
    words = re.findall(r"[a-z']+", t)
    negated_spans = {m.start(2) for m in _NEGATORS.finditer(t)}
    idx = 0
    for w in words:
        pos = t.find(w, idx)
        idx = pos + len(w)
        weight = _POS.get(w, 0.0) + _NEG.get(w, 0.0)
        if weight == 0.0:
            continue
        if pos in negated_spans:
            weight = -weight * 0.8  # "not approved" flips (damped)
        hits[w] = weight
        total += weight
    return math.tanh(total / 2.5), hits
