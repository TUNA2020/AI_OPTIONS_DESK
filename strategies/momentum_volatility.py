from __future__ import annotations

from strategies.gamma_scalping import Strategy as GammaScalp
from strategies.trend_credit_spread import Strategy as TrendCredit


class Strategy:
    name = "momentum_volatility"

    def build_trade(self, context, **kwargs):
        strength = float(context.get("trend_strength", 0.0))
        vix = float(context.get("vix", 0.0))
        if strength > 3 and vix > 16:
            return GammaScalp().build_trade(context, **kwargs)
        return TrendCredit().build_trade(context, **kwargs)
