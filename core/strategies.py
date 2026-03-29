"""
Strategies — Stratégies de trading pour NQ
"""

from loguru import logger


class Strategy:
    """Base class pour les stratégies"""

    def __init__(self, name: str):
        self.name = name
        self.enabled = True

    async def evaluate(self, analysis: dict) -> dict:
        """Évalue la stratégie et retourne un signal"""
        raise NotImplementedError


class VPOCReversion(Strategy):
    """
    Stratégie Mean Reversion sur VPOC
    - Prix s'éloigne du VPOC → attend retour
    - Entrée quand prix revient vers VPOC avec confirmation delta
    """

    def __init__(self):
        super().__init__("VPOC Reversion")
        self.min_distance = 5.0  # points minimum d'éloignement

    async def evaluate(self, analysis: dict) -> dict:
        signal = {'strategy': self.name, 'score': 0, 'direction': None}

        vpoc = analysis.get('vpoc', 0)
        price = analysis.get('current_price', 0)
        delta_bias = analysis.get('delta_bias', 'neutral')

        if vpoc == 0 or price == 0:
            return signal

        distance = price - vpoc

        # Prix au-dessus du VPOC → potentiel short (retour)
        if distance > self.min_distance and delta_bias == 'bearish':
            signal['direction'] = 'short'
            signal['score'] = min(80, int(abs(distance) * 3))
            signal['entry'] = price
            signal['reason'] = f"Prix {distance:.1f}pts au-dessus VPOC, delta bearish"

        # Prix en dessous du VPOC → potentiel long (retour)
        elif distance < -self.min_distance and delta_bias == 'bullish':
            signal['direction'] = 'long'
            signal['score'] = min(80, int(abs(distance) * 3))
            signal['entry'] = price
            signal['reason'] = f"Prix {abs(distance):.1f}pts sous VPOC, delta bullish"

        return signal


class DeltaDivergence(Strategy):
    """
    Stratégie Delta Divergence
    - Prix monte mais delta baisse → short
    - Prix baisse mais delta monte → long
    """

    def __init__(self):
        super().__init__("Delta Divergence")

    async def evaluate(self, analysis: dict) -> dict:
        signal = {'strategy': self.name, 'score': 0, 'direction': None}

        price = analysis.get('current_price', 0)
        delta_bias = analysis.get('delta_bias', 'neutral')
        bars = analysis.get('bars', {})

        if not bars or price == 0:
            return signal

        # Analyse tendance prix sur 5min
        bars_5m = bars.get('5min', [])
        if not bars_5m or len(bars_5m) < 3:
            return signal

        try:
            recent_closes = [b.close if hasattr(b, 'close') else b.get('close', 0)
                             for b in bars_5m[-3:]]
            price_trend = 'up' if recent_closes[-1] > recent_closes[0] else 'down'
        except (AttributeError, TypeError, IndexError):
            return signal

        # Divergence : prix monte + delta bearish → short
        if price_trend == 'up' and delta_bias == 'bearish':
            signal['direction'] = 'short'
            signal['score'] = 65
            signal['entry'] = price
            signal['reason'] = "Divergence : prix haussier, delta bearish"

        # Divergence : prix baisse + delta bullish → long
        elif price_trend == 'down' and delta_bias == 'bullish':
            signal['direction'] = 'long'
            signal['score'] = 65
            signal['entry'] = price
            signal['reason'] = "Divergence : prix baissier, delta bullish"

        return signal


class ImbalanceBreakout(Strategy):
    """
    Stratégie Order Flow Imbalance Breakout
    - Déséquilibre fort côté acheteur → long
    - Déséquilibre fort côté vendeur → short
    """

    def __init__(self):
        super().__init__("Imbalance Breakout")
        self.imbalance_threshold = 0.7  # 70% déséquilibre

    async def evaluate(self, analysis: dict) -> dict:
        signal = {'strategy': self.name, 'score': 0, 'direction': None}

        imbalance = analysis.get('imbalance', None)
        price = analysis.get('current_price', 0)

        if imbalance is None or price == 0:
            return signal

        # Extraction du ratio d'imbalance
        ratio = 0
        side = 'neutral'
        if hasattr(imbalance, 'ratio'):
            ratio = imbalance.ratio
            side = imbalance.dominant_side if hasattr(imbalance, 'dominant_side') else 'neutral'
        elif isinstance(imbalance, dict):
            ratio = imbalance.get('ratio', 0)
            side = imbalance.get('dominant_side', 'neutral')

        if ratio >= self.imbalance_threshold:
            if side == 'buy':
                signal['direction'] = 'long'
                signal['score'] = int(ratio * 100)
                signal['entry'] = price
                signal['reason'] = f"Imbalance acheteur : {ratio:.0%}"
            elif side == 'sell':
                signal['direction'] = 'short'
                signal['score'] = int(ratio * 100)
                signal['entry'] = price
                signal['reason'] = f"Imbalance vendeur : {ratio:.0%}"

        return signal


class StrategyManager:
    """Gère toutes les stratégies"""

    def __init__(self):
        self.strategies = [
            VPOCReversion(),
            DeltaDivergence(),
            ImbalanceBreakout(),
        ]

    async def evaluate_all(self, analysis: dict) -> list:
        """Évalue toutes les stratégies et retourne les signaux"""
        signals = []
        for strategy in self.strategies:
            if strategy.enabled:
                try:
                    signal = await strategy.evaluate(analysis)
                    if signal.get('score', 0) > 0:
                        signals.append(signal)
                except Exception as e:
                    logger.error(f"Erreur stratégie {strategy.name} : {e}")
        return sorted(signals, key=lambda s: s.get('score', 0), reverse=True)

    def get_strategies_info(self) -> list:
        return [{'name': s.name, 'enabled': s.enabled} for s in self.strategies]
