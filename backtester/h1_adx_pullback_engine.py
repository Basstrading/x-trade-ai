"""
BACKTEST H1 ADX + M5 SMA20 PULLBACK — NQ Nasdaq
=================================================
Regles:
  FILTRE H1:
    - Long  : close H1 > SMA20 H1
    - Short : close H1 < SMA20 H1
    - ADX(14) H1 > 22
    - Pente SMA20 H1 (delta 3 bougies) non plate

  SIGNAL M5:
    - Long  : low M5 <= SMA20 M5 - 15 pts (pullback sous la SMA)
    - Short : high M5 >= SMA20 M5 + 15 pts (pullback au-dessus de la SMA)
    - Entree a la cloture de la bougie M5

  GESTION:
    - TP fixe : 300 pts
    - Trailing stop long  : plus bas des 15 dernieres bougies M5
    - Trailing stop short : plus haut des 7 dernieres bougies M5
    - Sortie temps : 14h39 New York (20h39 Paris)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from loguru import logger

from backtester.opr_engine import is_dst_gap

POINT_VALUE = 20.0
TP_POINTS = 300.0
TRAIL_BARS_LONG = 15
TRAIL_BARS_SHORT = 7
SMA_PERIOD = 20
ADX_PERIOD = 14
ADX_THRESHOLD = 22
PULLBACK_DISTANCE = 15.0
SMA_SLOPE_BARS = 3


@dataclass
class PBTrade:
    date: str
    direction: str
    entry_price: float
    entry_time: str
    exit_price: float = 0.0
    exit_time: str = ''
    exit_reason: str = ''
    pnl_pts: float = 0.0
    pnl_usd: float = 0.0
    stop_price: float = 0.0


@dataclass
class PBReport:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_usd: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_usd: float = 0.0
    sharpe_ratio: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_trade: float = 0.0
    trades: List[dict] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)


def compute_adx(df_h1: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ADX on H1 dataframe."""
    high = df_h1['high']
    low = df_h1['low']
    close = df_h1['close']

    # +DM and -DM
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                        index=df_h1.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                         index=df_h1.index)

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Smoothed with Wilder's method (EMA with alpha=1/period)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr

    # DX and ADX
    di_sum = plus_di + minus_di
    di_sum = di_sum.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return adx


class H1ADXPullbackEngine:
    """Moteur backtest H1 ADX + M5 SMA20 Pullback."""

    def __init__(self, tp_points=TP_POINTS, trail_bars_long=TRAIL_BARS_LONG,
                 trail_bars_short=TRAIL_BARS_SHORT, sma_period=SMA_PERIOD,
                 adx_period=ADX_PERIOD, adx_threshold=ADX_THRESHOLD,
                 pullback_distance=PULLBACK_DISTANCE, sma_slope_bars=SMA_SLOPE_BARS,
                 point_value=POINT_VALUE, max_trades_day=99,
                 sma_slope_min=0.5):
        self.tp_points = tp_points
        self.trail_bars_long = trail_bars_long
        self.trail_bars_short = trail_bars_short
        self.sma_period = sma_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.pullback_distance = pullback_distance
        self.sma_slope_bars = sma_slope_bars
        self.point_value = point_value
        self.max_trades_day = max_trades_day
        self.sma_slope_min = sma_slope_min  # minimum absolute slope to be considered "not flat"

    def run(self, df_5min: pd.DataFrame, df_1h: Optional[pd.DataFrame] = None) -> Optional[PBReport]:
        """
        Lance le backtest.
        df_5min : DataFrame index=datetime(utc), colonnes open/high/low/close/volume
        df_1h   : idem en 1h (si None, resample depuis 5min)
        """
        import pytz
        PARIS = pytz.timezone('Europe/Paris')

        df = df_5min.copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')

        # Resample 1h si pas fourni
        if df_1h is None or len(df_1h) < self.sma_period + self.adx_period + 5:
            df_1h = df.resample('1h').agg({
                'open': 'first', 'high': 'max', 'low': 'min',
                'close': 'last', 'volume': 'sum'
            }).dropna()

        if df_1h.index.tz is None:
            df_1h.index = df_1h.index.tz_localize('UTC')

        # --- H1 indicators ---
        df_1h['sma20'] = df_1h['close'].rolling(self.sma_period).mean()
        df_1h['adx'] = compute_adx(df_1h, self.adx_period)
        # SMA slope: difference over sma_slope_bars candles
        df_1h['sma_slope'] = df_1h['sma20'] - df_1h['sma20'].shift(self.sma_slope_bars)

        # --- M5 indicators ---
        df['sma20'] = df['close'].rolling(self.sma_period).mean()

        # Paris time
        df['paris'] = df.index.tz_convert(PARIS)
        df['date'] = df['paris'].dt.date

        # Merge H1 data onto M5 (forward fill)
        df['sma20_1h'] = df_1h['sma20'].reindex(df.index, method='ffill')
        df['close_1h'] = df_1h['close'].reindex(df.index, method='ffill')
        df['adx_1h'] = df_1h['adx'].reindex(df.index, method='ffill')
        df['sma_slope_1h'] = df_1h['sma_slope'].reindex(df.index, method='ffill')

        # Drop rows without indicators
        df = df.dropna(subset=['sma20', 'sma20_1h', 'adx_1h', 'sma_slope_1h'])

        if len(df) < 50:
            logger.warning("Pas assez de donnees pour le backtest")
            return None

        trades: List[PBTrade] = []
        equity = [0.0]

        # Group by day
        for day, day_df in df.groupby('date'):
            if len(day_df) < self.sma_period:
                continue

            dst_gap = is_dst_gap(day)
            # 14h39 NY = 20h39 Paris (or 19h39 during DST gap)
            close_h, close_m = (19, 39) if dst_gap else (20, 39)
            # Start: allow trades from 15h30 Paris (14h30 DST gap)
            start_h, start_m = (14, 30) if dst_gap else (15, 30)

            trades_today = 0
            in_trade = False
            trade: Optional[PBTrade] = None
            trail_stop = 0.0

            for i in range(len(day_df)):
                row = day_df.iloc[i]
                paris_time = row['paris']
                h, m = paris_time.hour, paris_time.minute

                # Avant heure de debut — pas de trade
                if (h < start_h) or (h == start_h and m < start_m):
                    continue

                # Sortie forcee a l'heure limite
                if in_trade and ((h > close_h) or (h == close_h and m >= close_m)):
                    trade.exit_price = row['close']
                    trade.exit_time = str(paris_time)
                    trade.exit_reason = 'time'
                    pnl = (trade.exit_price - trade.entry_price) if trade.direction == 'long' else (trade.entry_price - trade.exit_price)
                    trade.pnl_pts = round(pnl, 2)
                    trade.pnl_usd = round(pnl * self.point_value, 2)
                    trades.append(trade)
                    equity.append(equity[-1] + trade.pnl_usd)
                    in_trade = False
                    trade = None
                    continue

                # Plus de trades apres heure limite
                if (h > close_h) or (h == close_h and m >= close_m):
                    continue

                # --- Gestion position ouverte ---
                if in_trade:
                    # TP check (intra-bar)
                    if trade.direction == 'long':
                        if row['high'] >= trade.entry_price + self.tp_points:
                            trade.exit_price = trade.entry_price + self.tp_points
                            trade.exit_time = str(paris_time)
                            trade.exit_reason = 'tp'
                            trade.pnl_pts = self.tp_points
                            trade.pnl_usd = round(self.tp_points * self.point_value, 2)
                            trades.append(trade)
                            equity.append(equity[-1] + trade.pnl_usd)
                            in_trade = False
                            trade = None
                            continue
                    else:
                        if row['low'] <= trade.entry_price - self.tp_points:
                            trade.exit_price = trade.entry_price - self.tp_points
                            trade.exit_time = str(paris_time)
                            trade.exit_reason = 'tp'
                            trade.pnl_pts = self.tp_points
                            trade.pnl_usd = round(self.tp_points * self.point_value, 2)
                            trades.append(trade)
                            equity.append(equity[-1] + trade.pnl_usd)
                            in_trade = False
                            trade = None
                            continue

                    # Trailing stop
                    if trade.direction == 'long':
                        lookback_start = max(0, i - self.trail_bars_long)
                        lookback = day_df.iloc[lookback_start:i + 1]
                        new_stop = lookback['low'].min()
                        trail_stop = max(trail_stop, new_stop) if trail_stop > 0 else new_stop
                        trade.stop_price = trail_stop
                        if row['low'] <= trail_stop:
                            trade.exit_price = trail_stop
                            trade.exit_time = str(paris_time)
                            trade.exit_reason = 'trail_stop'
                            pnl = trail_stop - trade.entry_price
                            trade.pnl_pts = round(pnl, 2)
                            trade.pnl_usd = round(pnl * self.point_value, 2)
                            trades.append(trade)
                            equity.append(equity[-1] + trade.pnl_usd)
                            in_trade = False
                            trade = None
                            continue
                    else:
                        lookback_start = max(0, i - self.trail_bars_short)
                        lookback = day_df.iloc[lookback_start:i + 1]
                        new_stop = lookback['high'].max()
                        trail_stop = min(trail_stop, new_stop) if trail_stop > 0 else new_stop
                        trade.stop_price = trail_stop
                        if row['high'] >= trail_stop:
                            trade.exit_price = trail_stop
                            trade.exit_time = str(paris_time)
                            trade.exit_reason = 'trail_stop'
                            pnl = trade.entry_price - trail_stop
                            trade.pnl_pts = round(pnl, 2)
                            trade.pnl_usd = round(pnl * self.point_value, 2)
                            trades.append(trade)
                            equity.append(equity[-1] + trade.pnl_usd)
                            in_trade = False
                            trade = None
                            continue

                    continue  # still in trade

                # --- Conditions d'entree ---
                if trades_today >= self.max_trades_day:
                    continue

                close_1h = row['close_1h']
                sma20_1h = row['sma20_1h']
                adx_1h = row['adx_1h']
                sma_slope = row['sma_slope_1h']
                sma20_5 = row['sma20']

                if pd.isna(sma20_5) or pd.isna(sma20_1h) or pd.isna(adx_1h) or pd.isna(sma_slope):
                    continue

                # === FILTRE H1 ===
                # ADX > seuil
                if adx_1h < self.adx_threshold:
                    continue

                # Pente SMA20 H1 non plate
                if abs(sma_slope) < self.sma_slope_min:
                    continue

                # Direction H1
                h1_long = close_1h > sma20_1h
                h1_short = close_1h < sma20_1h

                if not h1_long and not h1_short:
                    continue

                # === SIGNAL M5 ===
                direction = None
                if h1_long:
                    # Achat: le prix touche/depasse >= pullback_distance pts SOUS la SMA20 M5
                    if row['low'] <= sma20_5 - self.pullback_distance:
                        direction = 'long'
                elif h1_short:
                    # Vente: le prix touche/depasse >= pullback_distance pts AU-DESSUS de la SMA20 M5
                    if row['high'] >= sma20_5 + self.pullback_distance:
                        direction = 'short'

                if direction:
                    entry_price = row['close']

                    # Init trailing stop
                    if direction == 'long':
                        lookback_start = max(0, i - self.trail_bars_long)
                        lookback = day_df.iloc[lookback_start:i + 1]
                        trail_stop = lookback['low'].min()
                    else:
                        lookback_start = max(0, i - self.trail_bars_short)
                        lookback = day_df.iloc[lookback_start:i + 1]
                        trail_stop = lookback['high'].max()

                    trade = PBTrade(
                        date=str(day),
                        direction=direction,
                        entry_price=entry_price,
                        entry_time=str(paris_time),
                        stop_price=trail_stop,
                    )
                    in_trade = True
                    trades_today += 1

            # Fin de journee : fermer si encore en position
            if in_trade and trade:
                last_row = day_df.iloc[-1]
                trade.exit_price = last_row['close']
                trade.exit_time = str(last_row['paris'])
                trade.exit_reason = 'eod'
                pnl = (trade.exit_price - trade.entry_price) if trade.direction == 'long' else (trade.entry_price - trade.exit_price)
                trade.pnl_pts = round(pnl, 2)
                trade.pnl_usd = round(pnl * self.point_value, 2)
                trades.append(trade)
                equity.append(equity[-1] + trade.pnl_usd)
                in_trade = False

        if not trades:
            logger.warning("H1 ADX Pullback backtest: aucun trade genere")
            return None

        return self._build_report(trades, equity)

    def _build_report(self, trades: List[PBTrade], equity: List[float]) -> PBReport:
        pnls = [t.pnl_usd for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_pnl = sum(pnls)
        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0

        eq = np.array(equity)
        peak = np.maximum.accumulate(eq)
        dd = eq - peak
        max_dd = abs(dd.min()) if len(dd) > 0 else 0

        if len(pnls) > 1:
            sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252) if np.std(pnls) > 0 else 0, 2)
        else:
            sharpe = 0

        daily = {}
        for t in trades:
            d = t.date
            daily[d] = daily.get(d, 0) + t.pnl_usd

        report = PBReport(
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(len(wins) / len(trades) * 100, 1),
            total_pnl_usd=round(total_pnl, 2),
            avg_win=round(np.mean(wins), 2) if wins else 0,
            avg_loss=round(np.mean(losses), 2) if losses else 0,
            profit_factor=pf,
            max_drawdown_usd=round(max_dd, 2),
            sharpe_ratio=sharpe,
            best_trade=round(max(pnls), 2),
            worst_trade=round(min(pnls), 2),
            avg_trade=round(np.mean(pnls), 2),
            trades=[{
                'date': t.date,
                'direction': t.direction,
                'entry': t.entry_price,
                'exit': t.exit_price,
                'entry_time': t.entry_time,
                'exit_time': t.exit_time,
                'exit_reason': t.exit_reason,
                'pnl_pts': t.pnl_pts,
                'pnl_usd': t.pnl_usd,
                'stop': t.stop_price,
            } for t in trades],
            equity_curve=equity,
            daily_pnl={k: round(v, 2) for k, v in daily.items()},
        )

        return report
