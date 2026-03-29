"""
MOTEUR BACKTEST DALTON PUR
Basé sur : Mind Over Markets (Dalton)
Logique : IB → Open Type → Day Type → Signal → Money Management
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import pytz
from loguru import logger

POINT_VALUE = 20.0  # 1 pt NQ = $20
PARIS_TZ = pytz.timezone('Europe/Paris')


# ================================
# STRUCTURES DE DONNÉES
# ================================

@dataclass
class DayContext:
    """Contexte complet d'une journée de trading selon Dalton."""
    date: str = ''

    # Value Area jour précédent
    prev_poc: float = 0.0
    prev_vah: float = 0.0
    prev_val: float = 0.0

    # Initial Balance (15h30-16h30 Paris)
    ib_high: float = 0.0
    ib_low: float = 0.0
    ib_range: float = 0.0
    ib_avg_range: float = 0.0  # Moyenne IB des 10 derniers jours

    # Open Type Dalton
    open_type: str = ''
    open_price: float = 0.0

    # Day Type (classifié post-IB)
    day_type: str = ''

    # Extensions IB
    extended_up: bool = False
    extended_down: bool = False
    extension_count_up: int = 0
    extension_count_down: int = 0

    # Options
    gamma_condition: str = ''
    hvl: float = 0.0
    call_wall: float = 0.0
    put_wall: float = 0.0

    # État du jour
    ib_complete: bool = False
    day_type_set: bool = False


@dataclass
class DaltonTrade:
    entry_time: str = ''
    exit_time: str = ''
    direction: str = ''
    entry_price: float = 0.0
    exit_price: float = 0.0
    stop_price: float = 0.0
    pnl_points: float = 0.0
    pnl_dollars: float = 0.0
    exit_reason: str = ''
    signal_type: str = ''
    day_type: str = ''
    open_type: str = ''
    confidence: float = 0.0
    bars_held: int = 0
    target_price: float = 0.0
    stop_initial: float = 0.0


@dataclass
class DaltonReport:
    # Global
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_dollars: float = 0.0
    total_pnl_points: float = 0.0

    # Qualité
    avg_win_dollars: float = 0.0
    avg_loss_dollars: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    # Par signal Dalton
    trades_by_signal: dict = field(default_factory=dict)
    winrate_by_signal: dict = field(default_factory=dict)
    trades_by_daytype: dict = field(default_factory=dict)
    winrate_by_daytype: dict = field(default_factory=dict)

    # Exits
    exits_stop: int = 0
    exits_target: int = 0
    exits_poc: int = 0
    exits_session: int = 0
    exits_breakeven: int = 0
    exits_daily_limit: int = 0

    # Topstep
    days_traded: int = 0
    days_profitable: int = 0
    days_losing: int = 0
    best_day: float = 0.0
    worst_day: float = 0.0
    days_over_agent_limit: int = 0
    days_over_topstep_limit: int = 0

    # Projections
    avg_daily_pnl: float = 0.0
    projected_monthly: float = 0.0

    # Courbe
    equity_curve: list = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)


# ================================
# MOTEUR PRINCIPAL
# ================================

class DaltonEngine:

    def __init__(self, params=None):
        p = params or {}

        # Ratios stop / IB range — proportionnels à la volatilité du jour
        self.stop_ratio_trend = p.get('stop_ratio_trend', 0.08)
        self.stop_ratio_normal = p.get('stop_ratio_normal', 0.08)
        self.stop_ratio_neutral = p.get('stop_ratio_neutral', 0.06)
        self.stop_ratio_double = p.get('stop_ratio_double', 0.10)

        # Ratios trail / IB range
        self.trail_ratio_trend = p.get('trail_ratio_trend', 0.04)
        self.trail_ratio_double = p.get('trail_ratio_double', 0.05)

        # Ratio target (neutral day) — target = 25% IB depuis entry
        self.target_ratio_neutral = p.get('target_ratio_neutral', 0.25)

        # Stop minimum absolu — jamais moins de 8 pts
        self.stop_min_pts = p.get('stop_min_pts', 8)

        # Topstep $50k
        self.daily_loss_limit = p.get('daily_loss_limit', -900)
        self.max_trades_day = p.get('max_trades_day', 4)
        self.pause_after_stops = p.get('pause_after_stops', 2)

        # État
        self.trades = []
        self.equity = 0.0
        self.equity_curve = []
        self.daily_pnl_dict = {}

    # ================================
    # CALCUL VALUE AREA
    # ================================

    def calc_value_area(self, df: pd.DataFrame) -> dict:
        """Calcule POC/VAH/VAL sur un df."""
        if len(df) < 10:
            return {}

        price_vol = {}
        for _, bar in df.iterrows():
            p = round((bar['high'] + bar['low']) / 2, 1)
            v = bar.get('volume', 1)
            price_vol[p] = price_vol.get(p, 0) + v

        if not price_vol:
            return {}

        poc = max(price_vol, key=price_vol.get)
        total = sum(price_vol.values())
        target = total * 0.70

        sorted_pv = sorted(price_vol.items(), key=lambda x: x[1], reverse=True)
        cumvol = 0
        va_prices = []
        for price, vol in sorted_pv:
            cumvol += vol
            va_prices.append(price)
            if cumvol >= target:
                break

        if not va_prices:
            return {}

        return {
            'poc': float(poc),
            'vah': float(max(va_prices)),
            'val': float(min(va_prices)),
        }

    # ================================
    # OPEN TYPE (Dalton p.63-74)
    # ================================

    def classify_open_type(
        self,
        open_price: float,
        prev_vah: float,
        prev_val: float,
        prev_poc: float,
        first_bars: pd.DataFrame,
    ) -> str:
        """
        Classifie l'open type selon Dalton.
        OTD : Open Test Drive — conviction forte
        OTR : Open Test and Rejection — fade
        ORR : Open Range Rejection Reverse — suit la sortie
        OTO : Open Auction — neutre
        """
        if len(first_bars) < 5:
            return 'OTO'

        first_5 = first_bars.iloc[:5]
        high_5 = first_5['high'].max()
        low_5 = first_5['low'].min()
        range_5 = high_5 - low_5
        close_5 = first_5['close'].iloc[-1]

        in_prev_va = (prev_val <= open_price <= prev_vah)

        move = close_5 - open_price
        directional = abs(move) / max(range_5, 1)

        # OTD : conviction forte dès l'ouverture (60%+ du range dans une direction)
        if directional > 0.6 and range_5 > 0:
            return 'OTD_UP' if move > 0 else 'OTD_DOWN'

        # OTR : test et rejet d'un niveau
        if high_5 > prev_vah and close_5 < prev_vah:
            return 'OTR_DOWN'
        if low_5 < prev_val and close_5 > prev_val:
            return 'OTR_UP'

        # ORR : dans VA précédente puis sort
        if in_prev_va:
            if close_5 > prev_vah:
                return 'ORR_UP'
            if close_5 < prev_val:
                return 'ORR_DOWN'

        # OTO : neutre
        return 'OTO'

    # ================================
    # DAY TYPE (Dalton p.20-30)
    # ================================

    def classify_day_type(self, ctx: DayContext, current_bars: pd.DataFrame) -> str:
        """Classifie le type de journée après completion de l'IB."""
        if not ctx.ib_complete:
            return 'unknown'

        ib_range = ctx.ib_range
        ib_avg = ctx.ib_avg_range
        current_high = current_bars['high'].max()
        current_low = current_bars['low'].min()

        ext_up = current_high > ctx.ib_high
        ext_down = current_low < ctx.ib_low

        total_range = current_high - current_low
        range_vs_ib = total_range / max(ib_range, 1)

        # TREND DAY — Extension forte d'un seul côté
        if ext_up and not ext_down and range_vs_ib > 1.5:
            return 'trend_up'
        if ext_down and not ext_up and range_vs_ib > 1.5:
            return 'trend_down'

        # DOUBLE DISTRIBUTION — Extension des deux côtés avec direction dominante
        if ext_up and ext_down and range_vs_ib > 2.0:
            up_ext = current_high - ctx.ib_high
            down_ext = ctx.ib_low - current_low
            if up_ext > down_ext * 1.5:
                return 'double_dist_up'
            elif down_ext > up_ext * 1.5:
                return 'double_dist_down'

        # NORMAL DAY — Extension modérée
        if range_vs_ib <= 1.5:
            return 'normal'

        # NEUTRAL DAY — Extension des 2 côtés équilibrée
        if ext_up and ext_down:
            return 'neutral'

        # NONTREND — Reste dans l'IB
        if not ext_up and not ext_down:
            return 'nontrend'

        return 'normal'

    # ================================
    # SIGNAUX PAR TYPE DE JOURNÉE
    # ================================

    def get_signal(
        self,
        ctx: DayContext,
        bar: pd.Series,
        prev_bar: pd.Series,
        current_hour: int = 17,
        current_minute: int = 0,
    ) -> Optional[dict]:
        """Génère un signal selon le contexte Dalton du jour.
        Chaque signal a une fenêtre horaire valide (Paris)."""
        day_type = ctx.day_type
        open_type = ctx.open_type

        # === OTD BREAKOUT — Seulement après day_type classifié + compatible ===

        if open_type in ['OTD_UP', 'ORR_UP'] and ctx.day_type_set and not ctx.extended_up:
            if ctx.day_type in ['trend_up', 'double_dist_up', 'normal']:
                if 17 <= current_hour <= 18:
                    if bar['close'] > ctx.ib_high:
                        return {
                            'direction': 'long',
                            'signal': 'otd_breakout',
                            'confidence': 0.82,
                            'stop_ref': ctx.ib_low,
                            'target': 0,
                            'ib_high': ctx.ib_high,
                            'ib_low': ctx.ib_low,
                        }

        if open_type in ['OTD_DOWN', 'ORR_DOWN'] and ctx.day_type_set and not ctx.extended_down:
            if ctx.day_type in ['trend_down', 'double_dist_down', 'normal']:
                if 17 <= current_hour <= 18:
                    if bar['close'] < ctx.ib_low:
                        return {
                            'direction': 'short',
                            'signal': 'otd_breakout',
                            'confidence': 0.82,
                            'stop_ref': ctx.ib_high,
                            'target': 0,
                            'ib_high': ctx.ib_high,
                            'ib_low': ctx.ib_low,
                        }

        # === TREND DAY — Pullbacks 17h00-21h00 ===

        if day_type == 'trend_up' and 17 <= current_hour < 21:
            if (prev_bar['low'] <= ctx.ib_high
                    and bar['close'] > ctx.ib_high
                    and bar['close'] > prev_bar['close']):
                return {
                    'direction': 'long',
                    'signal': 'trend_pullback',
                    'confidence': 0.80,
                    'stop_ref': bar['low'],
                    'target': 0,
                    'ib_high': ctx.ib_high,
                    'ib_low': ctx.ib_low,
                }

        if day_type == 'trend_down' and 17 <= current_hour < 21:
            if (prev_bar['high'] >= ctx.ib_low
                    and bar['close'] < ctx.ib_low
                    and bar['close'] < prev_bar['close']):
                return {
                    'direction': 'short',
                    'signal': 'trend_pullback',
                    'confidence': 0.80,
                    'stop_ref': bar['high'],
                    'target': 0,
                    'ib_high': ctx.ib_high,
                    'ib_low': ctx.ib_low,
                }

        # === NORMAL DAY — Fade IB 17h00-21h00, prix proche IB (max 8 pts) ===

        if day_type in ['normal', 'unknown'] and 17 <= current_hour < 21:
            # Fade IB High — prix doit être dans les 8 pts de l'IB High
            if (bar['high'] >= ctx.ib_high
                    and bar['close'] < ctx.ib_high
                    and bar['close'] < prev_bar['close']
                    and abs(bar['close'] - ctx.ib_high) <= 8):
                poc_target = ctx.prev_poc if ctx.prev_poc > 0 else (ctx.ib_high + ctx.ib_low) / 2
                return {
                    'direction': 'short',
                    'signal': 'fade_ib_high',
                    'confidence': 0.75,
                    'stop_ref': bar['high'],
                    'target': poc_target,
                    'ib_high': ctx.ib_high,
                    'ib_low': ctx.ib_low,
                }

            # Fade IB Low — prix doit être dans les 8 pts de l'IB Low
            if (bar['low'] <= ctx.ib_low
                    and bar['close'] > ctx.ib_low
                    and bar['close'] > prev_bar['close']
                    and abs(bar['close'] - ctx.ib_low) <= 8):
                poc_target = ctx.prev_poc if ctx.prev_poc > 0 else (ctx.ib_high + ctx.ib_low) / 2
                return {
                    'direction': 'long',
                    'signal': 'fade_ib_low',
                    'confidence': 0.75,
                    'stop_ref': bar['low'],
                    'target': poc_target,
                    'ib_high': ctx.ib_high,
                    'ib_low': ctx.ib_low,
                }

        # === NEUTRAL DAY — Fade extrêmes 17h00-21h00, prix proche IB (max 8 pts) ===

        if day_type == 'neutral' and 17 <= current_hour < 21:
            mid = (ctx.ib_high + ctx.ib_low) / 2
            if (bar['high'] >= ctx.ib_high
                    and bar['close'] < ctx.ib_high
                    and abs(bar['close'] - ctx.ib_high) <= 8):
                return {
                    'direction': 'short',
                    'signal': 'neutral_fade_high',
                    'confidence': 0.65,
                    'stop_ref': bar['high'],
                    'target': mid,
                    'ib_high': ctx.ib_high,
                    'ib_low': ctx.ib_low,
                }
            if (bar['low'] <= ctx.ib_low
                    and bar['close'] > ctx.ib_low
                    and abs(bar['close'] - ctx.ib_low) <= 8):
                return {
                    'direction': 'long',
                    'signal': 'neutral_fade_low',
                    'confidence': 0.65,
                    'stop_ref': bar['low'],
                    'target': mid,
                    'ib_high': ctx.ib_high,
                    'ib_low': ctx.ib_low,
                }

        # === DOUBLE DISTRIBUTION — Breakout 17h30-21h00 ===

        if day_type == 'double_dist_up':
            if (17 <= current_hour < 21
                    and not (current_hour == 17 and current_minute < 30)):
                if bar['close'] > ctx.ib_high and prev_bar['close'] <= ctx.ib_high:
                    return {
                        'direction': 'long',
                        'signal': 'double_dist_break',
                        'confidence': 0.78,
                        'stop_ref': ctx.ib_high,
                        'target': 0,
                        'ib_high': ctx.ib_high,
                        'ib_low': ctx.ib_low,
                    }

        if day_type == 'double_dist_down':
            if (17 <= current_hour < 21
                    and not (current_hour == 17 and current_minute < 30)):
                if bar['close'] < ctx.ib_low and prev_bar['close'] >= ctx.ib_low:
                    return {
                        'direction': 'short',
                        'signal': 'double_dist_break',
                        'confidence': 0.78,
                        'stop_ref': ctx.ib_low,
                        'target': 0,
                        'ib_high': ctx.ib_high,
                        'ib_low': ctx.ib_low,
                    }

        return None

    # ================================
    # MONEY MANAGEMENT PAR DAY TYPE
    # ================================

    def get_mm(
        self, day_type: str, direction: str, entry: float, signal: dict,
        ib_range: float = 100.0, ib_high: float = 0.0, ib_low: float = 0.0,
    ) -> dict:
        """Retourne stop, target, trail proportionnels à l'IB du jour."""
        signal_type = signal.get('signal', '')
        target = signal.get('target', 0)

        # Sélection ratio par day type
        if day_type in ['double_dist_up', 'double_dist_down']:
            stop_ratio = self.stop_ratio_double
            trail_ratio = self.trail_ratio_double
            target_ratio = 0
        elif day_type in ['trend_up', 'trend_down']:
            stop_ratio = self.stop_ratio_trend
            trail_ratio = self.trail_ratio_trend
            target_ratio = 0
        elif day_type == 'neutral':
            stop_ratio = self.stop_ratio_neutral
            trail_ratio = 0
            target_ratio = self.target_ratio_neutral
        else:  # normal
            stop_ratio = self.stop_ratio_normal
            trail_ratio = 0
            target_ratio = 0

        # OTD breakout uses trend ratios
        if signal_type == 'otd_breakout':
            stop_ratio = self.stop_ratio_trend
            trail_ratio = self.trail_ratio_trend

        # Calcule le stop en points (proportionnel à l'IB, avec minimum)
        stop_pts = max(ib_range * stop_ratio, self.stop_min_pts)

        # Fades : stop au-delà de l'IB High/Low + buffer
        sig_ib_high = signal.get('ib_high', ib_high)
        sig_ib_low = signal.get('ib_low', ib_low)

        if signal_type in ['fade_ib_high', 'neutral_fade_high'] and sig_ib_high > 0 and direction == 'short':
            buf = max(stop_pts * 0.3, 5)
            stop = sig_ib_high + buf
        elif signal_type in ['fade_ib_low', 'neutral_fade_low'] and sig_ib_low > 0 and direction == 'long':
            buf = max(stop_pts * 0.3, 5)
            stop = sig_ib_low - buf
        elif direction == 'long':
            stop = entry - stop_pts
        else:
            stop = entry + stop_pts

        # Target dynamique pour neutral
        if target_ratio > 0 and not target:
            if direction == 'long':
                target = entry + ib_range * target_ratio
            else:
                target = entry - ib_range * target_ratio

        trail_pts = ib_range * trail_ratio if trail_ratio > 0 else 0

        return {
            'stop': stop,
            'target': target,
            'trail_pts': trail_pts,
            'stop_pts_used': round(stop_pts, 1),
            'size': 1,
        }

    # ================================
    # BOUCLE PRINCIPALE
    # ================================

    def run(
        self,
        df_1min: pd.DataFrame,
        df_5min: pd.DataFrame,
        options_levels: dict = None,
        daily_loss_limit: float = -900,
        max_trades_per_day: int = 4,
    ) -> Optional[DaltonReport]:

        self.daily_loss_limit = daily_loss_limit
        self.max_trades_day = max_trades_per_day

        # Convertit en heure Paris
        df = df_1min.copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        df.index = df.index.tz_convert(PARIS_TZ)

        df5 = df_5min.copy()
        if df5.index.tz is None:
            df5.index = df5.index.tz_localize('UTC')
        df5.index = df5.index.tz_convert(PARIS_TZ)

        # Groupes par journée
        daily_groups = {}
        for ts, row in df.iterrows():
            d = ts.date()
            if d not in daily_groups:
                daily_groups[d] = []
            daily_groups[d].append((ts, row))

        sorted_dates = sorted(daily_groups.keys())

        # IB avg range (10 derniers jours)
        ib_ranges_history = []

        # Résultats
        all_trades = []
        equity = 0.0
        equity_curve = [0.0]
        daily_pnl_dict = {}

        prev_va = {}  # Value area du jour précédent

        for date_idx, trade_date in enumerate(sorted_dates):
            bars_of_day = daily_groups[trade_date]
            if len(bars_of_day) < 30:
                continue

            # Init contexte du jour
            ctx = DayContext()
            ctx.date = str(trade_date)

            # Value area précédente
            ctx.prev_poc = prev_va.get('poc', 0)
            ctx.prev_vah = prev_va.get('vah', 0)
            ctx.prev_val = prev_va.get('val', 0)

            # Options
            if options_levels:
                ctx.hvl = options_levels.get('hvl', 0)
                ctx.call_wall = options_levels.get('call_wall', 0)
                ctx.put_wall = options_levels.get('put_wall', 0)
                price_now = bars_of_day[0][1]['close']
                ctx.gamma_condition = 'negative' if ctx.hvl > 0 and price_now < ctx.hvl else 'positive'

            # IB avg
            ctx.ib_avg_range = np.mean(ib_ranges_history[-10:]) if ib_ranges_history else 0

            # État journée
            daily_pnl = 0.0
            trades_today = 0
            consecutive_stops = 0
            pause_until = None

            # Trade state
            in_trade = False
            direction = ''
            entry_price = 0.0
            stop_price = 0.0
            target_price = 0.0
            trail_pts = 0.0
            entry_time = None
            signal_type = ''
            bars_held = 0
            stop_initial = 0.0
            mid_stop_set = False

            # Boucle barres du jour
            for bar_idx, (ts, bar) in enumerate(bars_of_day):
                hour = ts.hour
                minute = ts.minute

                # Filtre session 09h00 - 22h00 Paris
                if hour < 9 or hour >= 22:
                    continue

                # =====================
                # CALCUL IB (15h30-16h30)
                # =====================

                if (hour == 15 and minute >= 30) or (hour == 16 and minute < 30):
                    if not ctx.ib_complete:
                        # Open price = premier bar de la session US
                        if hour == 15 and minute == 30:
                            ctx.open_price = bar['open']

                        if ctx.ib_high == 0:
                            ctx.ib_high = bar['high']
                            ctx.ib_low = bar['low']
                        else:
                            ctx.ib_high = max(ctx.ib_high, bar['high'])
                            ctx.ib_low = min(ctx.ib_low, bar['low'])

                # IB complète à 16h30
                if hour == 16 and minute == 30 and not ctx.ib_complete and ctx.ib_high > 0:
                    ctx.ib_range = ctx.ib_high - ctx.ib_low
                    ctx.ib_complete = True
                    ib_ranges_history.append(ctx.ib_range)

                    # Classifie Open Type sur les barres 15h30-16h00
                    ib_bars = pd.DataFrame([r for t, r in bars_of_day if t.hour == 15 and t.minute >= 30])
                    if len(ib_bars) > 0:
                        ctx.open_type = self.classify_open_type(
                            ctx.open_price, ctx.prev_vah, ctx.prev_val, ctx.prev_poc, ib_bars
                        )

                    logger.debug(
                        f"{trade_date} IB: {ctx.ib_low:.0f}-{ctx.ib_high:.0f} "
                        f"({ctx.ib_range:.0f}pts) Open: {ctx.open_type}"
                    )

                # Avant IB complète : pas de trades
                if not ctx.ib_complete:
                    continue

                # =====================
                # CLASSIFIE DAY TYPE (17h00 Paris = 30 min post-IB)
                # =====================

                if not ctx.day_type_set and hour == 17 and minute == 0:
                    bars_so_far = pd.DataFrame([r for t, r in bars_of_day if t <= ts])
                    ctx.day_type = self.classify_day_type(ctx, bars_so_far)
                    ctx.day_type_set = True
                    logger.debug(f"{trade_date} Day Type: {ctx.day_type}")

                # NONTREND → pas de trade
                if ctx.day_type in ['nontrend', 'nonconviction']:
                    continue

                # Pause après stops consécutifs
                if pause_until and ts < pause_until:
                    continue

                # Limite journalière
                if daily_pnl <= self.daily_loss_limit:
                    if in_trade:
                        pnl_pts = self._pnl(entry_price, bar['close'], direction)
                        pnl_usd = pnl_pts * POINT_VALUE
                        equity += pnl_usd
                        daily_pnl += pnl_usd
                        self._record_trade(
                            all_trades, equity, entry_time, ts,
                            direction, entry_price, bar['close'],
                            stop_price, pnl_pts, 'daily_limit', signal_type,
                            ctx.day_type, ctx.open_type, 0.0, bars_held,
                            target_price, stop_initial
                        )
                        trades_today += 1
                        in_trade = False
                        equity_curve.append(equity)
                    continue

                # Max trades
                if trades_today >= self.max_trades_day:
                    continue

                # =====================
                # GESTION TRADE OUVERT
                # =====================

                if in_trade:
                    bars_held += 1
                    high = bar['high']
                    low = bar['low']

                    # Track extensions
                    if high > ctx.ib_high:
                        ctx.extended_up = True
                    if low < ctx.ib_low:
                        ctx.extended_down = True

                    # Trailing stop
                    if trail_pts > 0:
                        if direction == 'long':
                            new_stop = low - trail_pts
                            if new_stop > stop_price:
                                stop_price = new_stop
                        else:
                            new_stop = high + trail_pts
                            if new_stop < stop_price:
                                stop_price = new_stop

                    # Stop touché
                    stop_hit = ((direction == 'long' and low <= stop_price)
                                or (direction == 'short' and high >= stop_price))

                    if stop_hit:
                        exit_p = stop_price
                        pnl_pts = self._pnl(entry_price, exit_p, direction)
                        pnl_usd = pnl_pts * POINT_VALUE
                        equity += pnl_usd
                        daily_pnl += pnl_usd

                        if pnl_usd < 0:
                            consecutive_stops += 1
                            if consecutive_stops >= self.pause_after_stops:
                                from datetime import timedelta
                                pause_until = ts + timedelta(minutes=30)
                        else:
                            consecutive_stops = 0

                        reason = 'stop' if pnl_usd < 0 else 'breakeven'
                        self._record_trade(
                            all_trades, equity, entry_time, ts,
                            direction, entry_price, exit_p,
                            stop_price, pnl_pts, reason, signal_type,
                            ctx.day_type, ctx.open_type, 0.0, bars_held,
                            target_price, stop_initial
                        )
                        trades_today += 1
                        in_trade = False
                        equity_curve.append(equity)
                        continue

                    # Target atteint
                    if target_price > 0:
                        target_hit = ((direction == 'long' and high >= target_price)
                                      or (direction == 'short' and low <= target_price))
                        if target_hit:
                            pnl_pts = self._pnl(entry_price, target_price, direction)
                            pnl_usd = pnl_pts * POINT_VALUE
                            equity += pnl_usd
                            daily_pnl += pnl_usd
                            consecutive_stops = 0
                            self._record_trade(
                                all_trades, equity, entry_time, ts,
                                direction, entry_price, target_price,
                                stop_price, pnl_pts, 'target', signal_type,
                                ctx.day_type, ctx.open_type, 0.0, bars_held,
                                target_price, stop_initial
                            )
                            trades_today += 1
                            in_trade = False
                            equity_curve.append(equity)
                            continue

                    # Mid-stop breakeven (50% de entry→target)
                    if target_price > 0 and not mid_stop_set:
                        if direction == 'long':
                            mid = entry_price + (target_price - entry_price) * 0.5
                        else:
                            mid = entry_price - (entry_price - target_price) * 0.5
                        mid_hit = ((direction == 'long' and bar['close'] >= mid)
                                   or (direction == 'short' and bar['close'] <= mid))
                        if mid_hit:
                            if direction == 'long':
                                be = entry_price + 2
                                if be > stop_price:
                                    stop_price = be
                            else:
                                be = entry_price - 2
                                if be < stop_price:
                                    stop_price = be
                            mid_stop_set = True

                    # Fin session (21h30 Paris)
                    if hour == 21 and minute >= 30:
                        pnl_pts = self._pnl(entry_price, bar['close'], direction)
                        pnl_usd = pnl_pts * POINT_VALUE
                        equity += pnl_usd
                        daily_pnl += pnl_usd
                        self._record_trade(
                            all_trades, equity, entry_time, ts,
                            direction, entry_price, bar['close'],
                            stop_price, pnl_pts, 'session_end', signal_type,
                            ctx.day_type, ctx.open_type, 0.0, bars_held,
                            target_price, stop_initial
                        )
                        trades_today += 1
                        in_trade = False
                        equity_curve.append(equity)

                # =====================
                # RECHERCHE SIGNAL
                # =====================

                else:
                    if bar_idx < 1:
                        continue

                    prev_bar = bars_of_day[bar_idx - 1][1]

                    signal = self.get_signal(
                        ctx, bar, prev_bar,
                        current_hour=hour, current_minute=minute,
                    )

                    if signal is None:
                        continue

                    # Filtre confiance minimum
                    if signal['confidence'] < 0.65:
                        continue

                    # Filtre options : gamma négatif → réduit confiance fades
                    if ctx.gamma_condition == 'negative' and signal['signal'].startswith('fade'):
                        signal['confidence'] -= 0.10
                        if signal['confidence'] < 0.65:
                            continue

                    # Entre en trade
                    mm = self.get_mm(
                        ctx.day_type, signal['direction'], bar['close'], signal,
                        ib_range=ctx.ib_range, ib_high=ctx.ib_high, ib_low=ctx.ib_low,
                    )

                    in_trade = True
                    direction = signal['direction']
                    entry_price = bar['close']
                    entry_time = ts
                    stop_price = mm['stop']
                    stop_initial = mm['stop']
                    target_price = mm['target'] if mm['target'] > 0 else signal.get('target', 0)
                    trail_pts = mm['trail_pts']
                    signal_type = signal['signal']
                    bars_held = 0
                    mid_stop_set = False

            # Fin de journée — Calcule VA pour demain
            day_df = pd.DataFrame([r for t, r in bars_of_day])
            va = self.calc_value_area(day_df)
            if va:
                prev_va = va

            # Enregistre P&L du jour
            if daily_pnl != 0:
                daily_pnl_dict[str(trade_date)] = round(daily_pnl, 2)
                equity_curve.append(equity)

        return self._generate_report(all_trades, equity, equity_curve, daily_pnl_dict, daily_loss_limit)

    # ================================
    # HELPERS
    # ================================

    def _pnl(self, entry, exit_p, direction) -> float:
        if direction == 'long':
            return exit_p - entry
        return entry - exit_p

    def _record_trade(
        self, trades, equity,
        entry_time, exit_time,
        direction, entry, exit_p,
        stop, pnl_pts, reason,
        signal, day_type, open_type,
        confidence, bars_held,
        target, stop_initial
    ):
        pnl_usd = pnl_pts * POINT_VALUE
        trades.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_time),
            'direction': direction,
            'entry_price': round(entry, 2),
            'exit_price': round(exit_p, 2),
            'stop_price': round(stop, 2),
            'pnl_points': round(pnl_pts, 2),
            'pnl_dollars': round(pnl_usd, 2),
            'exit_reason': reason,
            'signal_type': signal,
            'day_type': day_type,
            'open_type': open_type,
            'confidence': confidence,
            'bars_held': bars_held,
            'target_price': round(target, 2),
            'stop_initial': round(stop_initial, 2),
        })

    def _generate_report(
        self, trades, equity, equity_curve, daily_pnl, daily_loss_limit
    ) -> Optional[DaltonReport]:
        if not trades:
            return None

        r = DaltonReport()
        r.trades = trades[-100:]
        r.equity_curve = equity_curve
        r.daily_pnl = daily_pnl

        pnls = [t['pnl_dollars'] for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        r.total_trades = len(trades)
        r.winning_trades = len(winners)
        r.losing_trades = len(losers)
        r.win_rate = round(len(winners) / len(trades) * 100, 1) if trades else 0
        r.total_pnl_dollars = round(sum(pnls), 2)
        r.total_pnl_points = round(sum(t['pnl_points'] for t in trades), 2)
        r.avg_win_dollars = round(np.mean(winners), 2) if winners else 0
        r.avg_loss_dollars = round(abs(np.mean(losers)), 2) if losers else 0

        gross_win = sum(winners) if winners else 0
        gross_loss = abs(sum(losers)) if losers else 0
        r.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else float('inf')
        r.expectancy = round(
            (r.avg_win_dollars * len(winners) / len(trades))
            - (r.avg_loss_dollars * len(losers) / len(trades)), 2
        ) if trades else 0

        # Max drawdown
        peak = 0.0
        max_dd = 0.0
        for e in equity_curve:
            if e > peak:
                peak = e
            dd = peak - e
            if dd > max_dd:
                max_dd = dd
        r.max_drawdown = round(max_dd, 2)

        # Sharpe
        if len(pnls) > 1:
            avg = np.mean(pnls)
            std = np.std(pnls)
            r.sharpe_ratio = round(avg / std * np.sqrt(252), 2) if std > 0 else 0
        else:
            r.sharpe_ratio = 0.0

        # Par signal
        for t in trades:
            s = t['signal_type']
            dt = t['day_type']
            r.trades_by_signal[s] = r.trades_by_signal.get(s, 0) + 1
            r.trades_by_daytype[dt] = r.trades_by_daytype.get(dt, 0) + 1

        # WR par signal
        for sig in r.trades_by_signal:
            sig_trades = [t for t in trades if t['signal_type'] == sig]
            wins = [t for t in sig_trades if t['pnl_dollars'] > 0]
            r.winrate_by_signal[sig] = round(len(wins) / len(sig_trades) * 100, 1) if sig_trades else 0

        # WR par daytype
        for dt in r.trades_by_daytype:
            dt_trades = [t for t in trades if t['day_type'] == dt]
            wins = [t for t in dt_trades if t['pnl_dollars'] > 0]
            r.winrate_by_daytype[dt] = round(len(wins) / len(dt_trades) * 100, 1) if dt_trades else 0

        # Exits
        for t in trades:
            reason = t['exit_reason']
            if reason == 'stop':
                r.exits_stop += 1
            elif reason == 'target':
                r.exits_target += 1
            elif reason == 'breakeven':
                r.exits_breakeven += 1
            elif reason == 'session_end':
                r.exits_session += 1
            elif reason == 'daily_limit':
                r.exits_daily_limit += 1

        # Topstep analyse
        if daily_pnl:
            pnl_vals = list(daily_pnl.values())
            r.days_traded = len(pnl_vals)
            r.days_profitable = sum(1 for p in pnl_vals if p > 0)
            r.days_losing = sum(1 for p in pnl_vals if p < 0)
            r.best_day = max(pnl_vals) if pnl_vals else 0
            r.worst_day = min(pnl_vals) if pnl_vals else 0
            r.days_over_agent_limit = sum(1 for p in pnl_vals if p < daily_loss_limit)
            r.days_over_topstep_limit = sum(1 for p in pnl_vals if p < -2000)
            r.avg_daily_pnl = round(np.mean(pnl_vals), 2)
            r.projected_monthly = round(r.avg_daily_pnl * 21, 2)

        return r
