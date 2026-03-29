"""
SignalAggregator — Agrège les signaux de toutes les stratégies
et produit un signal final avec score de confiance
"""

from loguru import logger


class SignalAggregator:

    def __init__(self, analyzer, strategies):
        self.analyzer = analyzer
        self.strategies = strategies
        self.min_score = 60  # Score minimum pour trader
        self.last_signal = None

    async def get_aggregated_signal(self) -> dict:
        """Analyse le marché et agrège les signaux"""
        # Récupère l'analyse marché
        analysis = await self.analyzer.get_full_analysis()

        # Enrichit avec les métriques résumées
        summary = await self.analyzer.get_summary()
        analysis.update(summary)

        # Évalue toutes les stratégies
        signals = await self.strategies.evaluate_all(analysis)

        if not signals:
            return {
                'action': 'hold',
                'score': 0,
                'direction': None,
                'signals': [],
                'analysis': summary,
            }

        # Agrège les signaux
        best_signal = signals[0]
        agreeing = [s for s in signals if s.get('direction') == best_signal.get('direction')]
        disagreeing = [s for s in signals if s.get('direction') and s.get('direction') != best_signal.get('direction')]

        # Score final = meilleur score + bonus confluence
        final_score = best_signal.get('score', 0)
        final_score += len(agreeing) * 10  # Bonus confluence
        final_score -= len(disagreeing) * 15  # Pénalité désaccord
        final_score = max(0, min(100, final_score))

        action = 'hold'
        if final_score >= self.min_score:
            action = 'trade'

        result = {
            'action': action,
            'score': final_score,
            'direction': best_signal.get('direction'),
            'entry': best_signal.get('entry', 0),
            'reason': best_signal.get('reason', ''),
            'strategy': best_signal.get('strategy', ''),
            'confluence': len(agreeing),
            'disagreements': len(disagreeing),
            'signals': signals,
            'analysis': summary,
        }

        self.last_signal = result
        return result

    def get_last_signal(self) -> dict:
        return self.last_signal or {'action': 'hold', 'score': 0}
