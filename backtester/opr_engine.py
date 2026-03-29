"""
MOTEUR BACKTEST OPR
Opening Price Range — 15h30-15h45 Paris
Basé sur stratégie NanoTrader/NASDAQ
Adapté Topstep $50k
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import pytz
from loguru import logger

POINT_VALUE = 20.0  # 1 pt NQ = $20
PARIS_TZ = pytz.timezone('Europe/Paris')


# === DST GAP DETECTION ===
# US ET Europe n'ont pas le meme calendrier DST.
# Gap spring : 2nd dimanche mars (US) -> dernier dimanche mars (EU)
# Gap fall   : dernier dimanche oct (EU) -> 1er dimanche nov (US)
# Pendant le gap, US market ouvre a 14h30 Paris au lieu de 15h30.

def _nth_sunday(year, month, n):
    """n-ieme dimanche du mois (1-indexed). n=-1 pour le dernier."""
    import calendar
    if n == -1:
        last_day = calendar.monthrange(year, month)[1]
        d = pd.Timestamp(year=year, month=month, day=last_day)
        while d.weekday() != 6:
            d -= pd.Timedelta(days=1)
        return d.date() if hasattr(d, 'date') else d
    from datetime import date as _date
    first = _date(year, month, 1)
    day_of_week = first.weekday()  # 0=mon
    first_sunday = 1 + (6 - day_of_week) % 7
    return _date(year, month, first_sunday + 7 * (n - 1))


def is_dst_gap(d) -> bool:
    """True si la date tombe dans le gap DST (US DST actif, EU pas encore)."""
    from datetime import date as _date
    if hasattr(d, 'date'):
        d = d.date() if callable(d.date) else d.date
    y = d.year
    # Spring gap: 2nd Sunday March -> last Sunday March (exclusive)
    us_spring = _nth_sunday(y, 3, 2)
    eu_spring = _nth_sunday(y, 3, -1)
    if us_spring <= d < eu_spring:
        return True
    # Fall gap: last Sunday Oct -> 1st Sunday Nov (exclusive)
    eu_fall = _nth_sunday(y, 10, -1)
    us_fall = _nth_sunday(y, 11, 1)
    if eu_fall <= d < us_fall:
        return True
    return False


def get_opr_schedule(d):
    """Retourne (range_start_h, range_start_m, range_end_h, range_end_m,
                 signal_start_h, signal_start_m, close_h, close_m)
       en heure Paris selon le decalage DST."""
    if is_dst_gap(d):
        # US DST actif, EU non -> market ouvre 14h30 Paris
        return (14, 30, 14, 45, 14, 45, 19, 49)
    else:
        # Normal -> market ouvre 15h30 Paris
        return (15, 30, 15, 45, 15, 45, 20, 49)


# === ATR & SUPERTREND ===

def calc_atr(df, period=1):
    """Average True Range."""
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def calc_supertrend(df, period=1, mult=3.4):
    """SuperTrend indicator. Returns Series of 1 (bullish) / -1 (bearish)."""
    hl2 = (df['high'] + df['low']) / 2
    atr = calc_atr(df, period)
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    direction = pd.Series(0, index=df.index, dtype=int)
    direction.iloc[0] = 1  # default bullish
    for i in range(1, len(df)):
        if df['close'].iloc[i] > upper.iloc[i - 1]:
            direction.iloc[i] = 1   # bullish
        elif df['close'].iloc[i] < lower.iloc[i - 1]:
            direction.iloc[i] = -1  # bearish
        else:
            direction.iloc[i] = direction.iloc[i - 1]
    return direction


@dataclass
class OPRTrade:
    entry_time: str = ''
    exit_time: str = ''
    direction: str = ''
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_points: float = 0.0
    pnl_dollars: float = 0.0
    exit_reason: str = ''  # tp / sl / time_exit
    range_high: float = 0.0
    range_low: float = 0.0
    range_size: float = 0.0
    bars_held: int = 0
    tp_pts: float = 0.0
    sl_pts: float = 0.0
    is_sar: bool = False


@dataclass
class OPRReport:
    # Global
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_dollars: float = 0.0
    total_pnl_points: float = 0.0
    avg_win_dollars: float = 0.0
    avg_loss_dollars: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    # Exits
    exits_tp: int = 0
    exits_sl: int = 0
    exits_time: int = 0

    # Stop & Reverse
    sar_trades: int = 0
    sar_wins: int = 0
    sar_pnl_dollars: float = 0.0

    # Long vs Short
    long_trades: int = 0
    long_winrate: float = 0.0
    short_trades: int = 0
    short_winrate: float = 0.0

    # Par range size
    avg_range_size: float = 0.0
    trades_small_range: int = 0   # range < 50pts
    trades_medium_range: int = 0  # 50-150pts
    trades_large_range: int = 0   # > 150pts

    # Topstep
    days_traded: int = 0
    days_profitable: int = 0
    days_losing: int = 0
    best_day: float = 0.0
    worst_day: float = 0.0
    days_over_agent_limit: int = 0
    days_over_topstep_limit: int = 0
    avg_daily_pnl: float = 0.0
    projected_monthly: float = 0.0

    # Courbe
    equity_curve: list = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)


class OPREngine:

    def __init__(self, params=None):
        p = params or {}

        # DST auto-detection (defaut True)
        self.auto_dst = p.get('auto_dst', True)

        # Range = 15h30 à 15h45 Paris (horaires normaux)
        # Overridden par jour si auto_dst=True
        self.range_start_hour = p.get('range_start_hour', 15)
        self.range_start_min = p.get('range_start_min', 30)
        self.range_end_hour = p.get('range_end_hour', 15)
        self.range_end_min = p.get('range_end_min', 45)

        # Signaux dès 15h45
        self.signal_start_hour = p.get('signal_start_hour', 15)
        self.signal_start_min = p.get('signal_start_min', 45)

        # Clôture forcée (défaut 20h49, configurable)
        self.close_hour = p.get('close_hour', 20)
        self.close_min = p.get('close_min', 49)

        # SL/TP en points (adaptés Topstep)
        self.tp_long = p.get('tp_long', 50)
        self.tp_short = p.get('tp_short', 40)
        self.sl_long = p.get('sl_long', 40)
        self.sl_short = p.get('sl_short', 30)

        # Limites journalières
        self.max_longs_per_day = p.get('max_longs', 5)
        self.max_shorts_per_day = p.get('max_shorts', 4)
        self.max_trades_per_day = p.get('max_trades', 4)
        self.daily_loss_limit = p.get('daily_loss_limit', -900)

        # Filtre range size
        self.min_range = p.get('min_range', 10)
        self.max_range = p.get('max_range', 200)

        # Buffer short : pts supplémentaires sous range_low pour valider un short
        self.short_buffer = p.get('short_buffer', 0)

        # Point value et contrats (NQ=20, MNQ=2)
        self.point_value = p.get('point_value', POINT_VALUE)
        self.contracts = p.get('contracts', 1)

        # SL dynamique PeriodsHighLow
        self.sl_type = p.get('sl_type', 'fixed')  # 'fixed' ou 'periods_high_low'
        self.sl_long_periods = p.get('sl_long_periods', 9)
        self.sl_long_delta = p.get('sl_long_delta', -41.75)
        self.sl_short_periods = p.get('sl_short_periods', 15)
        self.sl_short_delta = p.get('sl_short_delta', 0.25)
        self.sl_max_pts = p.get('sl_max_pts', 0)  # cap max SL dynamique (0 = pas de cap)

        # SuperTrend filter
        self.supertrend_enabled = p.get('supertrend_period', 0) > 0
        self.supertrend_period = p.get('supertrend_period', 1)
        self.supertrend_mult = p.get('supertrend_mult', 3.4)

        # SAR toggle
        self.sar_enabled = p.get('sar_enabled', True)

        # État
        self.trades = []
        self.filtered_trades = []  # trades bloqués par SuperTrend (avec simulation)
        self.equity = 0.0
        self.equity_curve = []
        self.daily_pnl_dict = {}

    def run(
        self,
        df_5min: pd.DataFrame,
        daily_loss_limit: float = -900,
        max_trades_per_day: int = 4,
    ) -> Optional[OPRReport]:

        self.daily_loss_limit = daily_loss_limit
        self.max_trades_per_day = max_trades_per_day

        # Convertit en heure Paris
        df = df_5min.copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        df.index = df.index.tz_convert(PARIS_TZ)

        # SuperTrend sur tout le df
        if self.supertrend_enabled:
            st_dir = calc_supertrend(df, self.supertrend_period, self.supertrend_mult)
            df['_st_dir'] = st_dir
        else:
            df['_st_dir'] = 1  # default bullish (no filter)

        # Index positionnel pour lookback SL dynamique
        df['_pos'] = range(len(df))

        # Groupe par journée
        daily_groups = {}
        for ts, row in df.iterrows():
            d = ts.date()
            if d not in daily_groups:
                daily_groups[d] = []
            daily_groups[d].append((ts, row))

        sorted_dates = sorted(daily_groups.keys())

        self.trades = []
        self.filtered_trades = []
        self.equity = 0.0
        self.equity_curve = [0.0]
        self.daily_pnl_dict = {}

        for trade_date in sorted_dates:
            bars = daily_groups[trade_date]
            if len(bars) < 5:
                continue

            # === DST AUTO-DETECTION PAR JOUR ===
            if self.auto_dst:
                sched = get_opr_schedule(trade_date)
                rsh, rsm = sched[0], sched[1]
                reh, rem_ = sched[2], sched[3]
                ssh, ssm = sched[4], sched[5]
                ch, cm = sched[6], sched[7]
            else:
                rsh, rsm = self.range_start_hour, self.range_start_min
                reh, rem_ = self.range_end_hour, self.range_end_min
                ssh, ssm = self.signal_start_hour, self.signal_start_min
                ch, cm = self.close_hour, self.close_min

            # === CALCUL DU RANGE OPR ===
            # Bougies 5min dans la fenetre OPR (3 bougies = 15 min)
            range_bars = [
                row for ts, row in bars
                if ts.hour == rsh and rsm <= ts.minute < rem_
            ]

            if len(range_bars) < 2:
                continue

            range_df = pd.DataFrame(range_bars)
            range_high = range_df['high'].max()
            range_low = range_df['low'].min()
            range_size = range_high - range_low

            # Filtre range size
            if range_size < self.min_range or range_size > self.max_range:
                continue

            # État journée
            daily_pnl = 0.0
            trades_today = 0
            longs_today = 0
            shorts_today = 0
            sar_today = 0  # Max 1 SAR par jour

            # Filtered trade simulation state (one at a time)
            filt_in_trade = False
            filt_direction = ''
            filt_entry_price = 0.0
            filt_tp_price = 0.0
            filt_sl_price = 0.0
            filt_sl_pts = 0.0
            filt_entry_time = None
            filt_bars_held = 0
            filt_range_high = 0.0
            filt_range_low = 0.0
            filt_range_size = 0.0
            filt_st_label = ''

            # Trade state
            in_trade = False
            direction = ''
            entry_price = 0.0
            tp_price = 0.0
            sl_price = 0.0
            entry_time = None
            bars_held = 0
            tp_pts = 0.0
            sl_pts = 0.0
            is_sar = False

            # SAR pending state (évalué sur la bougie suivante)
            sar_pending = False
            sar_direction = ''   # direction du SAR ('long' ou 'short')
            sar_level = 0.0      # prix du SL touché

            # Boucle barres après 15h45
            for ts, bar in bars:
                hour = ts.hour
                minute = ts.minute

                # Seulement apres signal_start (14h45 DST ou 15h45 normal)
                if hour < ssh or (hour == ssh and minute < ssm):
                    continue

                # Cloture forcee (19h49 DST ou 20h49 normal)
                force_close = (hour > ch or (hour == ch and minute >= cm))

                # Position dans le df global (pour SL dynamique)
                bar_pos = int(bar.get('_pos', 0))

                # === GESTION TRADE FILTRE (simulation parallèle) ===
                if filt_in_trade:
                    filt_bars_held += 1
                    f_tp_hit = ((filt_direction == 'long' and bar['high'] >= filt_tp_price)
                                or (filt_direction == 'short' and bar['low'] <= filt_tp_price))
                    f_sl_hit = ((filt_direction == 'long' and bar['low'] <= filt_sl_price)
                                or (filt_direction == 'short' and bar['high'] >= filt_sl_price))

                    if f_tp_hit and not f_sl_hit:
                        pnl_pts = self._pnl(filt_entry_price, filt_tp_price, filt_direction)
                        self.filtered_trades.append({
                            'entry_time': str(filt_entry_time), 'exit_time': str(ts),
                            'direction': filt_direction, 'entry_price': round(filt_entry_price, 2),
                            'exit_price': round(filt_tp_price, 2), 'tp_price': round(filt_tp_price, 2),
                            'sl_price': round(filt_sl_price, 2), 'sl_pts': round(filt_sl_pts, 1),
                            'pnl_points': round(pnl_pts, 2),
                            'pnl_dollars': round(pnl_pts * self.point_value * self.contracts, 2),
                            'exit_reason': 'tp', 'bars_held': filt_bars_held,
                            'range_size': round(filt_range_size, 2), 'st_direction': filt_st_label,
                        })
                        filt_in_trade = False
                    elif f_sl_hit:
                        pnl_pts = self._pnl(filt_entry_price, filt_sl_price, filt_direction)
                        self.filtered_trades.append({
                            'entry_time': str(filt_entry_time), 'exit_time': str(ts),
                            'direction': filt_direction, 'entry_price': round(filt_entry_price, 2),
                            'exit_price': round(filt_sl_price, 2), 'tp_price': round(filt_tp_price, 2),
                            'sl_price': round(filt_sl_price, 2), 'sl_pts': round(filt_sl_pts, 1),
                            'pnl_points': round(pnl_pts, 2),
                            'pnl_dollars': round(pnl_pts * self.point_value * self.contracts, 2),
                            'exit_reason': 'sl', 'bars_held': filt_bars_held,
                            'range_size': round(filt_range_size, 2), 'st_direction': filt_st_label,
                        })
                        filt_in_trade = False
                    elif force_close:
                        pnl_pts = self._pnl(filt_entry_price, bar['close'], filt_direction)
                        self.filtered_trades.append({
                            'entry_time': str(filt_entry_time), 'exit_time': str(ts),
                            'direction': filt_direction, 'entry_price': round(filt_entry_price, 2),
                            'exit_price': round(bar['close'], 2), 'tp_price': round(filt_tp_price, 2),
                            'sl_price': round(filt_sl_price, 2), 'sl_pts': round(filt_sl_pts, 1),
                            'pnl_points': round(pnl_pts, 2),
                            'pnl_dollars': round(pnl_pts * self.point_value * self.contracts, 2),
                            'exit_reason': 'time_exit', 'bars_held': filt_bars_held,
                            'range_size': round(filt_range_size, 2), 'st_direction': filt_st_label,
                        })
                        filt_in_trade = False

                # === CHECK SAR PENDING (bougie N+1 après SL) ===
                if sar_pending and not in_trade and not force_close:
                    sar_pending = False
                    if (sar_today < 1
                            and trades_today < self.max_trades_per_day
                            and daily_pnl > self.daily_loss_limit):
                        # CAS 1: SAR vers SHORT — bougie baissière sous le niveau SL
                        if (sar_direction == 'short'
                                and bar['close'] < bar['open']
                                and bar['close'] < sar_level
                                and shorts_today < self.max_shorts_per_day):
                            in_trade = True
                            direction = 'short'
                            entry_price = bar['close']
                            entry_time = ts
                            tp_pts = self.tp_short
                            tp_price = entry_price - self.tp_short
                            if self.sl_type == 'periods_high_low':
                                si = max(0, bar_pos - self.sl_short_periods)
                                rh = df['high'].iloc[si:bar_pos]
                                sl_pts = (rh.max() + self.sl_short_delta) - entry_price if len(rh) > 0 else self.sl_short
                                sl_pts = self._cap_sl(sl_pts)
                                sl_price = entry_price + sl_pts
                            else:
                                sl_pts = self.sl_short
                                sl_price = entry_price + self.sl_short
                            bars_held = 0
                            is_sar = True
                            sar_today += 1
                            continue
                        # CAS 2: SAR vers LONG — bougie haussière au-dessus du niveau SL
                        elif (sar_direction == 'long'
                                and bar['close'] > bar['open']
                                and bar['close'] > sar_level
                                and longs_today < self.max_longs_per_day):
                            in_trade = True
                            direction = 'long'
                            entry_price = bar['close']
                            entry_time = ts
                            tp_pts = self.tp_long
                            tp_price = entry_price + self.tp_long
                            if self.sl_type == 'periods_high_low':
                                si = max(0, bar_pos - self.sl_long_periods)
                                rl = df['low'].iloc[si:bar_pos]
                                sl_pts = entry_price - (rl.min() + self.sl_long_delta) if len(rl) > 0 else self.sl_long
                                sl_pts = self._cap_sl(sl_pts)
                                sl_price = entry_price - sl_pts
                            else:
                                sl_pts = self.sl_long
                                sl_price = entry_price - self.sl_long
                            bars_held = 0
                            is_sar = True
                            sar_today += 1
                            continue

                # === GESTION TRADE OUVERT ===
                if in_trade:
                    bars_held += 1

                    # Check TP (avant SL pour favoriser les wins sur même bougie)
                    tp_hit = (
                        (direction == 'long' and bar['high'] >= tp_price)
                        or (direction == 'short' and bar['low'] <= tp_price)
                    )

                    # Check SL
                    sl_hit = (
                        (direction == 'long' and bar['low'] <= sl_price)
                        or (direction == 'short' and bar['high'] >= sl_price)
                    )

                    if tp_hit and not sl_hit:
                        pnl_pts = self._pnl(entry_price, tp_price, direction)
                        self._record(
                            entry_time, ts, direction, entry_price, tp_price,
                            pnl_pts, 'tp', range_high, range_low, range_size,
                            bars_held, tp_pts, sl_pts, is_sar,
                        )
                        daily_pnl += pnl_pts * self.point_value * self.contracts
                        trades_today += 1
                        if direction == 'long':
                            longs_today += 1
                        else:
                            shorts_today += 1
                        in_trade = False
                        is_sar = False
                        continue

                    if sl_hit and not tp_hit:
                        sl_exit_price = sl_price
                        sl_direction = direction
                        pnl_pts = self._pnl(entry_price, sl_price, direction)
                        self._record(
                            entry_time, ts, direction, entry_price, sl_price,
                            pnl_pts, 'sl', range_high, range_low, range_size,
                            bars_held, tp_pts, sl_pts, is_sar,
                        )
                        daily_pnl += pnl_pts * self.point_value * self.contracts
                        trades_today += 1
                        if direction == 'long':
                            longs_today += 1
                        else:
                            shorts_today += 1
                        in_trade = False
                        is_sar = False

                        # Arme le SAR pour la bougie suivante
                        if self.sar_enabled and sar_today < 1 and not force_close:
                            sar_pending = True
                            sar_level = sl_exit_price
                            if sl_direction == 'long':
                                sar_direction = 'short'
                            else:
                                sar_direction = 'long'
                        continue

                    # Both TP and SL hit on same bar — conservative: assume SL
                    if tp_hit and sl_hit:
                        sl_exit_price = sl_price
                        sl_direction = direction
                        pnl_pts = self._pnl(entry_price, sl_price, direction)
                        self._record(
                            entry_time, ts, direction, entry_price, sl_price,
                            pnl_pts, 'sl', range_high, range_low, range_size,
                            bars_held, tp_pts, sl_pts, is_sar,
                        )
                        daily_pnl += pnl_pts * self.point_value * self.contracts
                        trades_today += 1
                        if direction == 'long':
                            longs_today += 1
                        else:
                            shorts_today += 1
                        in_trade = False
                        is_sar = False

                        # Arme le SAR pour la bougie suivante
                        if self.sar_enabled and sar_today < 1 and not force_close:
                            sar_pending = True
                            sar_level = sl_exit_price
                            if sl_direction == 'long':
                                sar_direction = 'short'
                            else:
                                sar_direction = 'long'
                        continue

                    # Clôture forcée
                    if force_close:
                        pnl_pts = self._pnl(entry_price, bar['close'], direction)
                        self._record(
                            entry_time, ts, direction, entry_price, bar['close'],
                            pnl_pts, 'time_exit', range_high, range_low,
                            range_size, bars_held, tp_pts, sl_pts, is_sar,
                        )
                        daily_pnl += pnl_pts * self.point_value * self.contracts
                        trades_today += 1
                        if direction == 'long':
                            longs_today += 1
                        else:
                            shorts_today += 1
                        in_trade = False
                        is_sar = False
                        continue

                # === RECHERCHE SIGNAL ===
                else:
                    if force_close:
                        continue

                    # Limites journée
                    if daily_pnl <= self.daily_loss_limit:
                        continue
                    if trades_today >= self.max_trades_per_day:
                        continue

                    # SuperTrend direction for this bar
                    st_dir = bar.get('_st_dir', 1)

                    # Signal LONG : clôture AU-DESSUS du range high
                    if (bar['close'] > range_high
                            and longs_today < self.max_longs_per_day):
                        # SuperTrend filter: LONG only if bullish
                        if self.supertrend_enabled and st_dir != 1:
                            if not filt_in_trade:
                                filt_in_trade = True
                                filt_direction = 'long'
                                filt_entry_price = bar['close']
                                filt_entry_time = ts
                                filt_tp_price = filt_entry_price + self.tp_long
                                filt_range_high = range_high
                                filt_range_low = range_low
                                filt_range_size = range_size
                                filt_st_label = 'bearish'
                                filt_bars_held = 0
                                if self.sl_type == 'periods_high_low':
                                    si = max(0, bar_pos - self.sl_long_periods)
                                    rl = df['low'].iloc[si:bar_pos]
                                    filt_sl_pts = filt_entry_price - (rl.min() + self.sl_long_delta) if len(rl) > 0 else self.sl_long
                                    filt_sl_pts = self._cap_sl(filt_sl_pts)
                                    filt_sl_price = filt_entry_price - filt_sl_pts
                                else:
                                    filt_sl_pts = self.sl_long
                                    filt_sl_price = filt_entry_price - self.sl_long
                        else:
                            in_trade = True
                            direction = 'long'
                            entry_price = bar['close']
                            entry_time = ts
                            tp_pts = self.tp_long
                            tp_price = entry_price + self.tp_long
                            # SL dynamique ou fixe
                            if self.sl_type == 'periods_high_low':
                                start_idx = max(0, bar_pos - self.sl_long_periods)
                                recent_lows = df['low'].iloc[start_idx:bar_pos]
                                if len(recent_lows) > 0:
                                    sl_pts = entry_price - (recent_lows.min() + self.sl_long_delta)
                                else:
                                    sl_pts = self.sl_long
                                sl_pts = self._cap_sl(sl_pts)
                                sl_price = entry_price - sl_pts
                            else:
                                sl_pts = self.sl_long
                                sl_price = entry_price - self.sl_long
                            bars_held = 0
                            continue

                    # Signal SHORT : clôture EN-DESSOUS du range low (- buffer)
                    if (bar['close'] < range_low - self.short_buffer
                            and shorts_today < self.max_shorts_per_day):
                        # SuperTrend filter: SHORT only if bearish
                        if self.supertrend_enabled and st_dir != -1:
                            if not filt_in_trade:
                                filt_in_trade = True
                                filt_direction = 'short'
                                filt_entry_price = bar['close']
                                filt_entry_time = ts
                                filt_tp_price = filt_entry_price - self.tp_short
                                filt_range_high = range_high
                                filt_range_low = range_low
                                filt_range_size = range_size
                                filt_st_label = 'bullish'
                                filt_bars_held = 0
                                if self.sl_type == 'periods_high_low':
                                    si = max(0, bar_pos - self.sl_short_periods)
                                    rh = df['high'].iloc[si:bar_pos]
                                    filt_sl_pts = (rh.max() + self.sl_short_delta) - filt_entry_price if len(rh) > 0 else self.sl_short
                                    filt_sl_pts = self._cap_sl(filt_sl_pts)
                                    filt_sl_price = filt_entry_price + filt_sl_pts
                                else:
                                    filt_sl_pts = self.sl_short
                                    filt_sl_price = filt_entry_price + self.sl_short
                        else:
                            in_trade = True
                            direction = 'short'
                            entry_price = bar['close']
                            entry_time = ts
                            tp_pts = self.tp_short
                            tp_price = entry_price - self.tp_short
                            # SL dynamique ou fixe
                            if self.sl_type == 'periods_high_low':
                                start_idx = max(0, bar_pos - self.sl_short_periods)
                                recent_highs = df['high'].iloc[start_idx:bar_pos]
                                if len(recent_highs) > 0:
                                    sl_pts = (recent_highs.max() + self.sl_short_delta) - entry_price
                                else:
                                    sl_pts = self.sl_short
                                sl_pts = self._cap_sl(sl_pts)
                                sl_price = entry_price + sl_pts
                            else:
                                sl_pts = self.sl_short
                                sl_price = entry_price + self.sl_short
                            bars_held = 0
                            continue

            # Fin de journée — clôturer trade ouvert
            if in_trade:
                last_ts, last_bar = bars[-1]
                pnl_pts = self._pnl(entry_price, last_bar['close'], direction)
                self._record(
                    entry_time, last_ts, direction, entry_price, last_bar['close'],
                    pnl_pts, 'time_exit', range_high, range_low, range_size,
                    bars_held, tp_pts, sl_pts, is_sar,
                )
                daily_pnl += pnl_pts * self.point_value * self.contracts
                in_trade = False

            # Fin de journée — clôturer trade filtré simulé
            if filt_in_trade:
                last_ts, last_bar = bars[-1]
                pnl_pts = self._pnl(filt_entry_price, last_bar['close'], filt_direction)
                self.filtered_trades.append({
                    'entry_time': str(filt_entry_time), 'exit_time': str(last_ts),
                    'direction': filt_direction, 'entry_price': round(filt_entry_price, 2),
                    'exit_price': round(last_bar['close'], 2), 'tp_price': round(filt_tp_price, 2),
                    'sl_price': round(filt_sl_price, 2), 'sl_pts': round(filt_sl_pts, 1),
                    'pnl_points': round(pnl_pts, 2),
                    'pnl_dollars': round(pnl_pts * self.point_value * self.contracts, 2),
                    'exit_reason': 'eod', 'bars_held': filt_bars_held,
                    'range_size': round(filt_range_size, 2), 'st_direction': filt_st_label,
                })
                filt_in_trade = False

            # Enregistre P&L jour
            if daily_pnl != 0:
                self.daily_pnl_dict[str(trade_date)] = round(daily_pnl, 2)
                self.equity += daily_pnl
                self.equity_curve.append(self.equity)

        return self._generate_report(daily_loss_limit)

    def _cap_sl(self, sl_pts):
        """Cap le SL dynamique si sl_max_pts est défini."""
        if self.sl_max_pts > 0 and sl_pts > self.sl_max_pts:
            return self.sl_max_pts
        return sl_pts

    def _pnl(self, entry, exit_p, direction) -> float:
        if direction == 'long':
            return exit_p - entry
        return entry - exit_p

    def _record(
        self, entry_time, exit_time, direction, entry, exit_p,
        pnl_pts, reason, range_high, range_low, range_size,
        bars_held, tp_pts, sl_pts, is_sar=False,
    ):
        pnl_usd = pnl_pts * self.point_value * self.contracts
        self.trades.append({
            'entry_time': str(entry_time),
            'exit_time': str(exit_time),
            'direction': direction,
            'entry_price': round(entry, 2),
            'exit_price': round(exit_p, 2),
            'pnl_points': round(pnl_pts, 2),
            'pnl_dollars': round(pnl_usd, 2),
            'exit_reason': reason,
            'range_high': round(range_high, 2),
            'range_low': round(range_low, 2),
            'range_size': round(range_size, 2),
            'bars_held': bars_held,
            'tp_pts': tp_pts,
            'sl_pts': sl_pts,
            'is_sar': is_sar,
        })

    def _generate_report(self, daily_loss_limit) -> Optional[OPRReport]:
        if not self.trades:
            return None

        r = OPRReport()
        r.trades = self.trades
        r.equity_curve = self.equity_curve
        r.daily_pnl = self.daily_pnl_dict

        pnls = [t['pnl_dollars'] for t in self.trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        r.total_trades = len(self.trades)
        r.winning_trades = len(winners)
        r.losing_trades = len(losers)
        r.win_rate = round(len(winners) / len(self.trades) * 100, 1)
        r.total_pnl_dollars = round(sum(pnls), 2)
        r.total_pnl_points = round(sum(t['pnl_points'] for t in self.trades), 2)
        r.avg_win_dollars = round(np.mean(winners) if winners else 0, 2)
        r.avg_loss_dollars = round(abs(np.mean(losers)) if losers else 0, 2)

        gross_win = sum(winners)
        gross_loss = abs(sum(losers))
        r.profit_factor = round(
            gross_win / gross_loss if gross_loss > 0 else float('inf'), 2
        )
        r.expectancy = round(np.mean(pnls), 2)

        # Max drawdown
        peak = 0.0
        max_dd = 0.0
        for e in self.equity_curve:
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
            r.sharpe_ratio = round(avg / std * np.sqrt(252) if std > 0 else 0, 2)

        # Exits
        for t in self.trades:
            reason = t['exit_reason']
            if reason == 'tp':
                r.exits_tp += 1
            elif reason == 'sl':
                r.exits_sl += 1
            elif reason == 'time_exit':
                r.exits_time += 1

        # Stop & Reverse stats
        sar_trades = [t for t in self.trades if t.get('is_sar')]
        r.sar_trades = len(sar_trades)
        r.sar_wins = sum(1 for t in sar_trades if t['pnl_dollars'] > 0)
        r.sar_pnl_dollars = round(sum(t['pnl_dollars'] for t in sar_trades), 2)

        # Long vs Short
        longs = [t for t in self.trades if t['direction'] == 'long']
        shorts = [t for t in self.trades if t['direction'] == 'short']
        r.long_trades = len(longs)
        r.short_trades = len(shorts)
        if longs:
            r.long_winrate = round(
                len([t for t in longs if t['pnl_dollars'] > 0]) / len(longs) * 100, 1
            )
        if shorts:
            r.short_winrate = round(
                len([t for t in shorts if t['pnl_dollars'] > 0]) / len(shorts) * 100, 1
            )

        # Range stats
        ranges = [t['range_size'] for t in self.trades]
        r.avg_range_size = round(np.mean(ranges) if ranges else 0, 1)
        r.trades_small_range = sum(1 for x in ranges if x < 50)
        r.trades_medium_range = sum(1 for x in ranges if 50 <= x < 150)
        r.trades_large_range = sum(1 for x in ranges if x >= 150)

        # Topstep
        if self.daily_pnl_dict:
            pnl_vals = list(self.daily_pnl_dict.values())
            r.days_traded = len(pnl_vals)
            r.days_profitable = sum(1 for p in pnl_vals if p > 0)
            r.days_losing = sum(1 for p in pnl_vals if p < 0)
            r.best_day = max(pnl_vals)
            r.worst_day = min(pnl_vals)
            r.days_over_agent_limit = sum(1 for p in pnl_vals if p < daily_loss_limit)
            r.days_over_topstep_limit = sum(1 for p in pnl_vals if p < -2000)
            r.avg_daily_pnl = round(np.mean(pnl_vals), 2)
            r.projected_monthly = round(r.avg_daily_pnl * 21, 2)

        return r
