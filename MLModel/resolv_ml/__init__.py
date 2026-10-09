"""resolv_ml, learns how prediction-market prices map to real outcomes.

The question this package answers: given what Kalshi or Polymarket is charging
for a contract right now, and how that price got here, what is the probability
it actually resolves YES?

The market price is the baseline. Everything here is judged on whether it beats
that baseline out of sample, on markets that resolved after everything it was
trained on.
"""
__version__ = "1.0.0"
