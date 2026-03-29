"""
MarketAnalyzer — Analyse de marché via projectx-api
Récupère les barres multi-timeframe et calcule les indicateurs
Volume Profile, Delta, Support/Résistance — depuis l'ouverture de session NQ
"""

from loguru import logger
from datetime import datetime, timedelta
from projectx_api import AggregationUnit
import numpy as np
import pytz


# --- Session NQ pour trader en France ---

PARIS_TZ = pytz.timezone('Europe/Paris')


def get_session_info() -> dict:
    """Calcule les minutes depuis l'ouverture de session NQ (heure Paris).
    - Session overnight : 00:00 Paris (18:00 ET veille)
    - Session principale US : 15:30 Paris (09:30 ET)
    Retourne {start_hour, start_minute, minutes, label}
    """
    now_paris = datetime.now(PARIS_TZ)
    h, m = now_paris.hour, now_paris.minute

    if h >= 15 and (h > 15 or m >= 30):
        # Session principale US ouverte (15:30+ Paris)
        session_open = now_paris.replace(hour=15, minute=30, second=0, microsecond=0)
        session_label = "US Main"
    else:
        # Session overnight (depuis minuit Paris)
        session_open = now_paris.replace(hour=0, minute=0, second=0, microsecond=0)
        session_label = "Overnight"

    delta = now_paris - session_open
    minutes = max(30, int(delta.total_seconds() / 60))

    start_str = session_open.strftime('%H:%M')
    now_str = now_paris.strftime('%H:%M')

    return {
        'start_hour': session_open.hour,
        'start_minute': session_open.minute,
        'minutes': minutes,
        'label': session_label,
        'display': f"{start_str} -> {now_str} ({minutes} min)",
    }


def get_session_minutes() -> int:
    """Raccourci : retourne le nombre de minutes depuis l'ouverture de session."""
    return get_session_info()['minutes']


class MarketAnalyzer:

    def __init__(self, client, contract_id: str, account_id: int):
        self.client = client
        self.contract_id = contract_id
        self.account_id = account_id

    async def get_bars(self, unit=AggregationUnit.MINUTE, unit_number=1,
                       minutes_back=60, limit=60) -> list:
        """Récupère les barres historiques"""
        if not self.client or not self.contract_id:
            return []
        try:
            now = datetime.utcnow()
            bars = await self.client.retrieve_bars(
                contractId=self.contract_id,
                live=False,
                startTime=now - timedelta(minutes=minutes_back),
                endTime=now,
                unit=unit,
                unitNumber=unit_number,
                limit=limit,
                includePartialBar=True
            )
            return bars or []
        except Exception as e:
            logger.error(f"Erreur barres : {e}")
            return []

    async def get_full_analysis(self) -> dict:
        """Analyse complète depuis l'ouverture de session NQ"""
        analysis = {}

        try:
            session = get_session_info()
            session_min = session['minutes']
            analysis['session'] = session

            # Barres session complète pour VP et Delta
            # Pour sessions longues (>120 min), utilise barres 5min pour le VP
            if session_min > 120:
                vp_bars = await self.get_bars(
                    AggregationUnit.MINUTE, 5, session_min,
                    limit=max(60, session_min // 5)
                )
            else:
                vp_bars = await self.get_bars(
                    AggregationUnit.MINUTE, 1, session_min,
                    limit=max(60, session_min)
                )

            # Barres 1min récentes pour prix actuel + imbalance
            bars_1m = await self.get_bars(AggregationUnit.MINUTE, 1, 60, 60)

            # Barres multi-timeframe (pour stratégies)
            bars_5m = await self.get_bars(AggregationUnit.MINUTE, 5, 300, 60)
            bars_15m = await self.get_bars(AggregationUnit.MINUTE, 15, 900, 60)

            analysis['bars'] = {
                '1min': bars_1m,
                '5min': bars_5m,
                '15min': bars_15m,
            }

            # Prix actuel (dernière barre 1min)
            if bars_1m:
                last = bars_1m[-1]
                analysis['current_price'] = last.get('close', last.get('c', 0))
            else:
                analysis['current_price'] = 0

            # Volume Profile depuis ouverture de session
            if vp_bars and len(vp_bars) > 5:
                analysis['volume_profile'] = self._calc_volume_profile(vp_bars)
                analysis['vpoc'] = analysis['volume_profile'].get('poc_price', 0)
                analysis['vah'] = analysis['volume_profile'].get('value_area_high', 0)
                analysis['val'] = analysis['volume_profile'].get('value_area_low', 0)

            # Cumulative Delta depuis ouverture de session
            # Utilise toutes les barres session pour le delta, pas juste les 15 dernières
            delta_bars = vp_bars if vp_bars and len(vp_bars) > 5 else bars_1m
            if delta_bars and len(delta_bars) > 5:
                delta_info = self._calc_cumulative_delta(delta_bars)
                analysis['cumulative_delta'] = delta_info
                analysis['delta_bias'] = delta_info.get('bias', 'neutral')

            # Imbalance sur les 10 dernières barres 1min (court terme)
            if bars_1m and len(bars_1m) > 5:
                analysis['imbalance'] = self._calc_imbalance(bars_1m[-10:])

        except Exception as e:
            logger.error(f"Erreur analyse : {e}")
            analysis['error'] = str(e)

        return analysis

    def _calc_volume_profile(self, bars: list) -> dict:
        """Calcule un volume profile simplifié"""
        if not bars:
            return {}

        prices = []
        volumes = []
        for b in bars:
            close = b.get('close', b.get('c', 0))
            high = b.get('high', b.get('h', close))
            low = b.get('low', b.get('l', close))
            vol = b.get('volume', b.get('v', 1))
            mid = (high + low) / 2
            prices.append(mid)
            volumes.append(vol)

        if not prices:
            return {}

        prices = np.array(prices)
        volumes = np.array(volumes)

        # Crée 20 bins de prix
        price_min, price_max = prices.min(), prices.max()
        if price_min == price_max:
            return {'poc_price': price_min, 'value_area_high': price_min, 'value_area_low': price_min}

        bins = np.linspace(price_min, price_max, 21)
        vol_at_price = np.zeros(20)

        for p, v in zip(prices, volumes):
            idx = min(int((p - price_min) / (price_max - price_min) * 20), 19)
            vol_at_price[idx] += v

        # POC = prix avec le plus de volume
        poc_idx = np.argmax(vol_at_price)
        poc_price = (bins[poc_idx] + bins[poc_idx + 1]) / 2

        # Value Area (70% du volume)
        total_vol = vol_at_price.sum()
        target_vol = total_vol * 0.7
        sorted_indices = np.argsort(vol_at_price)[::-1]
        cumul = 0
        va_indices = []
        for idx in sorted_indices:
            cumul += vol_at_price[idx]
            va_indices.append(idx)
            if cumul >= target_vol:
                break

        va_low_idx = min(va_indices)
        va_high_idx = max(va_indices)

        return {
            'poc_price': round(poc_price, 2),
            'value_area_high': round(bins[va_high_idx + 1], 2),
            'value_area_low': round(bins[va_low_idx], 2),
            'total_volume': int(total_vol),
        }

    def _calc_cumulative_delta(self, bars: list) -> dict:
        """Calcule le cumulative delta simplifié (close - open)"""
        if not bars:
            return {'delta': 0, 'bias': 'neutral'}

        cumulative = 0
        for b in bars:
            close = b.get('close', b.get('c', 0))
            open_p = b.get('open', b.get('o', 0))
            vol = b.get('volume', b.get('v', 1))
            # Delta simplifié : si close > open → volume acheteur, sinon vendeur
            if close > open_p:
                cumulative += vol
            elif close < open_p:
                cumulative -= vol

        bias = 'neutral'
        if cumulative > 500:
            bias = 'bullish'
        elif cumulative < -500:
            bias = 'bearish'

        return {'delta': cumulative, 'bias': bias}

    def _calc_imbalance(self, bars: list) -> dict:
        """Calcule un ratio d'imbalance simplifié"""
        if not bars:
            return {'ratio': 0, 'dominant_side': 'neutral'}

        buy_vol = 0
        sell_vol = 0
        for b in bars:
            close = b.get('close', b.get('c', 0))
            open_p = b.get('open', b.get('o', 0))
            vol = b.get('volume', b.get('v', 1))
            if close >= open_p:
                buy_vol += vol
            else:
                sell_vol += vol

        total = buy_vol + sell_vol
        if total == 0:
            return {'ratio': 0, 'dominant_side': 'neutral'}

        ratio = max(buy_vol, sell_vol) / total
        side = 'buy' if buy_vol > sell_vol else 'sell'

        return {'ratio': round(ratio, 3), 'dominant_side': side}

    async def get_vpoc(self) -> float:
        """Retourne le VPOC depuis l'ouverture de session"""
        session_min = get_session_minutes()
        bars = await self.get_bars(AggregationUnit.MINUTE, 5, session_min,
                                   limit=max(60, session_min // 5))
        vp = self._calc_volume_profile(bars)
        return vp.get('poc_price', 0)

    async def get_delta_bias(self) -> str:
        """Retourne le biais delta depuis l'ouverture de session"""
        session_min = get_session_minutes()
        bars = await self.get_bars(AggregationUnit.MINUTE, 5, session_min,
                                   limit=max(60, session_min // 5))
        delta = self._calc_cumulative_delta(bars)
        return delta.get('bias', 'neutral')

    async def get_vah_val(self) -> tuple:
        """Retourne (VAH, VAL) depuis l'ouverture de session"""
        session_min = get_session_minutes()
        bars = await self.get_bars(AggregationUnit.MINUTE, 5, session_min,
                                   limit=max(60, session_min // 5))
        vp = self._calc_volume_profile(bars)
        return vp.get('value_area_high', 0), vp.get('value_area_low', 0)

    async def get_summary(self) -> dict:
        """Résumé rapide pour le dashboard"""
        analysis = await self.get_full_analysis()

        price = analysis.get('current_price', 0)
        vpoc = analysis.get('vpoc', 0)
        vah = analysis.get('vah', 0)
        val = analysis.get('val', 0)
        delta_bias = analysis.get('delta_bias', 'neutral')
        session = analysis.get('session', get_session_info())

        price_vs_vpoc = 'at'
        if vpoc > 0 and price > 0:
            if price > vpoc + 2:
                price_vs_vpoc = 'above'
            elif price < vpoc - 2:
                price_vs_vpoc = 'below'

        return {
            'price': price,
            'vpoc': vpoc,
            'vah': vah,
            'val': val,
            'delta_bias': delta_bias,
            'price_vs_vpoc': price_vs_vpoc,
            'session': session.get('display', ''),
            'session_label': session.get('label', ''),
            'session_minutes': session.get('minutes', 0),
        }
