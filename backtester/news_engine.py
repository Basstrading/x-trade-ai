"""
NEWS TRADING ENGINE — Backtest
================================
Strategie: Avant une news majeure, detecter la direction du marche
et entrer dans cette direction avec un stop large.

Logique:
1. X minutes avant la news (lookback_min), analyser la direction
2. Direction = close actuel vs close il y a lookback_min minutes
3. Si clairement haussier -> LONG, si clairement baissier -> SHORT
4. Stop large (wide_sl_pts), TP genereux (tp_pts)
5. Sortie apres max_hold_min minutes si ni TP ni SL touche
"""
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np


@dataclass
class NewsTrade:
    date: str
    news_event: str
    news_time_et: str
    direction: str
    entry_time: str
    entry: float
    exit_time: str = ''
    exit: float = 0.0
    pnl_pts: float = 0.0
    pnl_usd: float = 0.0
    exit_reason: str = ''


@dataclass
class NewsBacktestReport:
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl_usd: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_usd: float = 0.0
    avg_trade: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    sharpe_ratio: float = 0.0
    trades: list = field(default_factory=list)


class NewsBacktestEngine:
    def __init__(self,
                 lookback_min: int = 30,
                 min_move_pts: float = 10,
                 entry_before_min: int = 5,
                 wide_sl_pts: float = 150,
                 tp_pts: float = 200,
                 trail_bars: int = 0,
                 max_hold_min: int = 60,
                 point_value: float = 8.0,
                 tier_filter: int = 1,
                 use_sma_direction: bool = False,
                 sma_period: int = 20):
        """
        lookback_min: minutes avant la news pour analyser la direction
        min_move_pts: mouvement minimum requis pour confirmer une direction
        entry_before_min: minutes avant la news pour entrer
        wide_sl_pts: stop loss en points
        tp_pts: take profit en points
        trail_bars: trailing stop (0 = pas de trail, N = low/high des N dernières bougies 5min)
        max_hold_min: duree max de la position apres l'entree
        point_value: valeur du point ($8 = 4 MNQ)
        tier_filter: 1 = NFP/CPI/FOMC seulement, 2 = + PPI/ISM/GDP/Retail, 3 = tous
        use_sma_direction: utiliser SMA pour determiner la direction au lieu du delta de prix
        sma_period: periode SMA si use_sma_direction
        """
        self.lookback_min = lookback_min
        self.min_move_pts = min_move_pts
        self.entry_before_min = entry_before_min
        self.wide_sl_pts = wide_sl_pts
        self.tp_pts = tp_pts
        self.trail_bars = trail_bars
        self.max_hold_min = max_hold_min
        self.point_value = point_value
        self.tier_filter = tier_filter
        self.use_sma_direction = use_sma_direction
        self.sma_period = sma_period

    def run(self, df: pd.DataFrame, calendar_path: str = None) -> Optional[NewsBacktestReport]:
        """
        df: DataFrame 5min OHLCV avec index datetime UTC
        calendar_path: chemin vers news_calendar_clean.csv
        """
        if calendar_path is None:
            calendar_path = str(Path(__file__).resolve().parent.parent / 'data' / 'news_calendar_clean.csv')

        cal = pd.read_csv(calendar_path)
        cal = cal[cal['tier'] <= self.tier_filter]

        # Ensure df index is UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')

        # Precompute SMA if needed
        if self.use_sma_direction:
            df['sma'] = df['close'].rolling(self.sma_period).mean()

        trades = []

        for _, event_row in cal.iterrows():
            date_str = event_row['date']
            time_et = event_row['time_et']
            event_name = event_row['events']

            # Convert ET time to UTC
            # ET = UTC-5 (standard) or UTC-4 (DST: March-November)
            hour_et, minute_et = map(int, time_et.split(':'))

            try:
                event_dt_et = pd.Timestamp(date_str + ' ' + time_et,
                                           tz='America/New_York')
                event_dt_utc = event_dt_et.tz_convert('UTC')
            except Exception:
                continue

            # Entry time = entry_before_min minutes before news
            entry_dt = event_dt_utc - pd.Timedelta(minutes=self.entry_before_min)
            # Lookback start
            lookback_dt = event_dt_utc - pd.Timedelta(minutes=self.lookback_min)

            # Find bars in our data
            mask_lookback = (df.index >= lookback_dt) & (df.index < entry_dt)
            lookback_bars = df[mask_lookback]

            if len(lookback_bars) < 2:
                continue

            # Determine direction
            direction = None
            if self.use_sma_direction:
                last_close = lookback_bars['close'].iloc[-1]
                last_sma = lookback_bars['sma'].iloc[-1]
                if pd.isna(last_sma):
                    continue
                if last_close > last_sma + self.min_move_pts:
                    direction = 'long'
                elif last_close < last_sma - self.min_move_pts:
                    direction = 'short'
            else:
                # Simple: compare close at entry vs close at lookback start
                price_start = lookback_bars['close'].iloc[0]
                price_end = lookback_bars['close'].iloc[-1]
                move = price_end - price_start

                if move >= self.min_move_pts:
                    direction = 'long'
                elif move <= -self.min_move_pts:
                    direction = 'short'

            if direction is None:
                continue

            # Find entry bar
            entry_mask = df.index >= entry_dt
            if not entry_mask.any():
                continue
            entry_idx = df.index[entry_mask][0]
            entry_bar = df.loc[entry_idx]
            entry_price = entry_bar['close']

            # Set stops
            if direction == 'long':
                sl = entry_price - self.wide_sl_pts
                tp = entry_price + self.tp_pts
            else:
                sl = entry_price + self.wide_sl_pts
                tp = entry_price - self.tp_pts

            # Max exit time
            max_exit_dt = entry_dt + pd.Timedelta(minutes=self.max_hold_min)

            # Simulate trade bar by bar
            trade = NewsTrade(
                date=date_str,
                news_event=event_name,
                news_time_et=time_et,
                direction=direction,
                entry_time=str(entry_idx),
                entry=entry_price
            )

            exit_reason = ''
            exit_price = entry_price
            exit_time = str(entry_idx)

            # Get bars from entry to max_exit
            trade_mask = (df.index > entry_idx) & (df.index <= max_exit_dt)
            trade_bars = df[trade_mask]

            trail_stop = sl
            bar_count = 0

            for bar_time, bar in trade_bars.iterrows():
                bar_count += 1

                if direction == 'long':
                    # Check SL
                    if bar['low'] <= sl:
                        exit_price = sl
                        exit_reason = 'sl'
                        exit_time = str(bar_time)
                        break

                    # Check trail stop
                    if self.trail_bars > 0 and bar_count >= self.trail_bars:
                        recent = trade_bars.iloc[max(0, bar_count - self.trail_bars):bar_count]
                        new_trail = recent['low'].min()
                        if new_trail > trail_stop:
                            trail_stop = new_trail
                        if bar['low'] <= trail_stop and trail_stop > sl:
                            exit_price = trail_stop
                            exit_reason = 'trail'
                            exit_time = str(bar_time)
                            break

                    # Check TP
                    if bar['high'] >= tp:
                        exit_price = tp
                        exit_reason = 'tp'
                        exit_time = str(bar_time)
                        break

                else:  # short
                    # Check SL
                    if bar['high'] >= sl:
                        exit_price = sl
                        exit_reason = 'sl'
                        exit_time = str(bar_time)
                        break

                    # Check trail stop
                    if self.trail_bars > 0 and bar_count >= self.trail_bars:
                        recent = trade_bars.iloc[max(0, bar_count - self.trail_bars):bar_count]
                        new_trail = recent['high'].max()
                        if new_trail < trail_stop:
                            trail_stop = new_trail
                        if bar['high'] >= trail_stop and trail_stop < sl:
                            exit_price = trail_stop
                            exit_reason = 'trail'
                            exit_time = str(bar_time)
                            break

                    # Check TP
                    if bar['low'] <= tp:
                        exit_price = tp
                        exit_reason = 'tp'
                        exit_time = str(bar_time)
                        break

            else:
                # Time exit
                if len(trade_bars) > 0:
                    exit_price = trade_bars['close'].iloc[-1]
                    exit_time = str(trade_bars.index[-1])
                exit_reason = 'time'

            # Calculate PnL
            if direction == 'long':
                pnl_pts = exit_price - entry_price
            else:
                pnl_pts = entry_price - exit_price

            trade.exit = exit_price
            trade.exit_time = exit_time
            trade.pnl_pts = round(pnl_pts, 2)
            trade.pnl_usd = round(pnl_pts * self.point_value, 2)
            trade.exit_reason = exit_reason

            trades.append(trade)

        if not trades:
            return None

        # Build report
        report = NewsBacktestReport()
        report.trades = [t.__dict__ for t in trades]
        report.total_trades = len(trades)

        pnls = [t.pnl_usd for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        report.total_pnl_usd = round(sum(pnls), 2)
        report.win_rate = round(len(wins) / len(pnls) * 100, 1) if pnls else 0
        report.avg_trade = round(np.mean(pnls), 2) if pnls else 0
        report.avg_win = round(np.mean(wins), 2) if wins else 0
        report.avg_loss = round(np.mean(losses), 2) if losses else 0

        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.01
        report.profit_factor = round(gross_win / gross_loss, 2)

        # Max drawdown
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        report.max_drawdown_usd = round(np.max(dd), 2) if len(dd) > 0 else 0

        # Sharpe
        if len(pnls) > 1 and np.std(pnls) > 0:
            report.sharpe_ratio = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2)
        else:
            report.sharpe_ratio = 0

        return report
