"""Resolv pricing engine — deterministic, dependency-free.

Public surface:
    from resolv_engine import price, Market, Evidence, QualRubric
"""
from .models import Contribution, Evidence, Market, PriceResult, QualRubric
from .pricing import (digital_option_prob, debias_market_prob, evidence_log_odds,
                      kelly_no, kelly_yes, norm_cdf, price, realized_vol)
from .sentiment import score as sentiment_score

__all__ = [
    "Contribution", "Evidence", "Market", "PriceResult", "QualRubric",
    "digital_option_prob", "debias_market_prob", "evidence_log_odds",
    "kelly_no", "kelly_yes", "norm_cdf", "price", "realized_vol",
    "sentiment_score",
]
__version__ = "0.1.0"
