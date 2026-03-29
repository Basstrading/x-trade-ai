"""
BACKTEST MM20 TREND FOLLOWING — NQ Nasdaq
==========================================
Regles:
  LONG  : cloture 5min > SMA20 5min  ET  cloture 1h > SMA20 1h
  SHORT : cloture 5min < SMA20 5min  ET  cloture 1h < SMA20 1h

Gestion:
  - Stop suiveur : plus bas des 9 dernieres bougies 5min (long) / plus haut (short)
  - Take profit  : +200 pts
  - Sortie temps : 20h39 Paris (19h39 pendant gap DST)
  - Debut trades : 15h30 Paris (14h30 pendant gap DST)
  - Max 4 trades / jour
  - 1 lot NQ (20$/pt)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from loguru import logger

from backtester.opr_engine import is_dst_gap

POINT_VALUE = 20.0
TP_POINTS = 200.0
TRAIL_BARS = 9
MAX_TRADES_DAY = 4
SMA_PERIOD = 20


@dataclass
class MM20Trade:
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
class MM20Report:
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


class MM20BacktestEngine:
    """Moteur backtest MM20 trend following."""

    def __init__(self, tp_points=TP_POINTS, trail_bars=TRAIL_BARS,
                 max_trades_day=MAX_TRADES_DAY, sma_period=SMA_PERIOD,
                 start_offset_min=0, min_sma_dist=0.0, max_sma_dist=0.0,
                 atr_min=0.0, daily_loss_stop=0, point_value=POINT_VALUE,
                 daily_loss_usd=0, pullback_bars=0, pullback_dist=0.0,
                 trail_bars_short=0, trail_delta_short=0.0, max_sl_pts=0.0,
                 adx_threshold=0.0, sma_slope_min=0.0, sma_slope_bars=3,
                 abs_start_hour=0, abs_start_min=0,
                 breakeven_pts=0.0, candle_dir_filter=False,
                 min_h1_sma_dist=0.0, trail_delta_long=0.0):
        self.tp_points = tp_points
        self.trail_bars = trail_bars
        self.max_trades_day = max_trades_day
        self.sma_period = sma_period
        self.start_offset_min = start_offset_min
        self.min_sma_dist = min_sma_dist
        self.max_sma_dist = max_sma_dist
        self.atr_min = atr_min
        self.daily_loss_stop = daily_loss_stop
        self.point_value = point_value
        self.daily_loss_usd = daily_loss_usd  # cap perte journaliere en $ (0 = desactive)
        self.pullback_bars = pullback_bars    # lookback bars pour detecter un pullback (0 = desactive)
        self.pullback_dist = pullback_dist    # distance max du low/high a la SMA pour valider pullback
        self.trail_bars_short = trail_bars_short if trail_bars_short > 0 else trail_bars  # lookback short (0 = meme que long)
        self.trail_delta_short = trail_delta_short  # pts ajoutes au-dessus du high pour le stop short
        self.max_sl_pts = max_sl_pts          # stop loss max en pts depuis entry (0 = desactive)
        self.adx_threshold = adx_threshold    # ADX H1 minimum (0 = desactive)
        self.sma_slope_min = sma_slope_min    # pente min SMA20 H1 (0 = desactive)
        self.sma_slope_bars = sma_slope_bars  # nb bougies pour calculer la pente
        self.abs_start_hour = abs_start_hour  # heure absolue Paris (0 = utilise start_offset)
        self.abs_start_min = abs_start_min
        self.breakeven_pts = breakeven_pts      # apres X pts de gain, stop passe a entry (0 = desactive)
        self.trail_delta_long = trail_delta_long  # pts soustraits du low pour le stop long (ex: -45)
        self.candle_dir_filter = candle_dir_filter  # bougie M5 doit cloturer dans le sens du trade
        self.min_h1_sma_dist = min_h1_sma_dist  # distance min close H1 vs SMA20 H1 (0 = desactive)

    def run(self, df_5min: pd.DataFrame, df_1h: Optional[pd.DataFrame] = None) -> Optional[MM20Report]:
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
        if df_1h is None or len(df_1h) < self.sma_period + 5:
            df_1h = df.resample('1h').agg({
                'open': 'first', 'high': 'max', 'low': 'min',
                'close': 'last', 'volume': 'sum'
            }).dropna()

        if df_1h.index.tz is None:
            df_1h.index = df_1h.index.tz_localize('UTC')

        # SMA20
        df['sma20'] = df['close'].rolling(self.sma_period).mean()
        df_1h['sma20'] = df_1h['close'].rolling(self.sma_period).mean()

        # ADX H1 + Pente SMA20 H1 (si actives)
        if self.adx_threshold > 0:
            from backtester.h1_adx_pullback_engine import compute_adx
            df_1h['adx'] = compute_adx(df_1h, 14)
        if self.sma_slope_min > 0:
            df_1h['sma_slope'] = df_1h['sma20'] - df_1h['sma20'].shift(self.sma_slope_bars)

        # ATR 14
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs(),
        ], axis=1).max(axis=1)
        df['atr14'] = tr.rolling(14).mean()

        # Paris time
        df['paris'] = df.index.tz_convert(PARIS)
        df['date'] = df['paris'].dt.date

        # Merge 1h sma onto 5min (forward fill)
        df['sma20_1h'] = df_1h['sma20'].reindex(df.index, method='ffill')
        df['close_1h'] = df_1h['close'].reindex(df.index, method='ffill')
        if self.adx_threshold > 0:
            df['adx_1h'] = df_1h['adx'].reindex(df.index, method='ffill')
        if self.sma_slope_min > 0:
            df['sma_slope_1h'] = df_1h['sma_slope'].reindex(df.index, method='ffill')

        # Drop rows without sma
        df = df.dropna(subset=['sma20', 'sma20_1h'])

        if len(df) < 50:
            logger.warning("Pas assez de donnees pour le backtest MM20")
            return None

        trades: List[MM20Trade] = []
        equity = [0.0]

        # Group by day
        for day, day_df in df.groupby('date'):
            if len(day_df) < self.sma_period:
                continue

            dst_gap = is_dst_gap(day)
            if self.abs_start_hour > 0:
                start_h, start_m = self.abs_start_hour, self.abs_start_min
            else:
                base_h, base_m = (14, 30) if dst_gap else (15, 30)
                total_min = base_h * 60 + base_m + self.start_offset_min
                start_h, start_m = total_min // 60, total_min % 60
            close_h, close_m = (19, 39) if dst_gap else (20, 39)

            trades_today = 0
            consec_losses = 0
            daily_pnl_today = 0.0
            in_trade = False
            trade: Optional[MM20Trade] = None
            trail_stop = 0.0

            for i in range(len(day_df)):
                row = day_df.iloc[i]
                paris_time = row['paris']
                h, m = paris_time.hour, paris_time.minute

                # Avant 15h30 (ou 14h30 DST) — pas de trade
                if (h < start_h) or (h == start_h and m < start_m):
                    continue

                # Sortie forcee 20h39 (ou 19h39 DST)
                if in_trade and ((h > close_h) or (h == close_h and m >= close_m)):
                    trade.exit_price = row['close']
                    trade.exit_time = str(paris_time)
                    trade.exit_reason = 'time'
                    pnl = (trade.exit_price - trade.entry_price) if trade.direction == 'long' else (trade.entry_price - trade.exit_price)
                    trade.pnl_pts = round(pnl, 2)
                    trade.pnl_usd = round(pnl * self.point_value, 2)
                    trades.append(trade)
                    equity.append(equity[-1] + trade.pnl_usd)
                    daily_pnl_today += trade.pnl_usd
                    consec_losses = consec_losses + 1 if pnl < 0 else 0
                    in_trade = False
                    trade = None
                    continue

                # Plus de trades apres 20h39
                if (h > close_h) or (h == close_h and m >= close_m):
                    continue

                # --- Gestion position ouverte ---
                if in_trade:
                    price = row['close']

                    # TP check (intra-bar on high/low)
                    if trade.direction == 'long':
                        if row['high'] >= trade.entry_price + self.tp_points:
                            trade.exit_price = trade.entry_price + self.tp_points
                            trade.exit_time = str(paris_time)
                            trade.exit_reason = 'tp'
                            trade.pnl_pts = self.tp_points
                            trade.pnl_usd = round(self.tp_points * self.point_value, 2)
                            trades.append(trade)
                            equity.append(equity[-1] + trade.pnl_usd)
                            daily_pnl_today += trade.pnl_usd
                            consec_losses = 0
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
                            daily_pnl_today += trade.pnl_usd
                            consec_losses = 0
                            in_trade = False
                            trade = None
                            continue

                    # Breakeven stop: apres X pts de gain, le trail ne descend plus sous entry
                    if self.breakeven_pts > 0:
                        if trade.direction == 'long':
                            if row['high'] >= trade.entry_price + self.breakeven_pts:
                                trail_stop = max(trail_stop, trade.entry_price)
                        else:
                            if row['low'] <= trade.entry_price - self.breakeven_pts:
                                trail_stop = min(trail_stop, trade.entry_price) if trail_stop > 0 else trade.entry_price

                    # Stop loss max fixe (protection krach)
                    if self.max_sl_pts > 0:
                        if trade.direction == 'long':
                            hard_stop = trade.entry_price - self.max_sl_pts
                            if row['low'] <= hard_stop:
                                trade.exit_price = hard_stop
                                trade.exit_time = str(paris_time)
                                trade.exit_reason = 'max_sl'
                                pnl = -self.max_sl_pts
                                trade.pnl_pts = round(pnl, 2)
                                trade.pnl_usd = round(pnl * self.point_value, 2)
                                trades.append(trade)
                                equity.append(equity[-1] + trade.pnl_usd)
                                daily_pnl_today += trade.pnl_usd
                                consec_losses += 1
                                in_trade = False
                                trade = None
                                continue
                        else:
                            hard_stop = trade.entry_price + self.max_sl_pts
                            if row['high'] >= hard_stop:
                                trade.exit_price = hard_stop
                                trade.exit_time = str(paris_time)
                                trade.exit_reason = 'max_sl'
                                pnl = -self.max_sl_pts
                                trade.pnl_pts = round(pnl, 2)
                                trade.pnl_usd = round(pnl * self.point_value, 2)
                                trades.append(trade)
                                equity.append(equity[-1] + trade.pnl_usd)
                                daily_pnl_today += trade.pnl_usd
                                consec_losses += 1
                                in_trade = False
                                trade = None
                                continue

                    # Trailing stop : low/high des N dernieres bougies 5min
                    if trade.direction == 'long':
                        lookback_start = max(0, i - self.trail_bars)
                        lookback = day_df.iloc[lookback_start:i + 1]
                        new_stop = lookback['low'].min() - self.trail_delta_long
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
                            daily_pnl_today += trade.pnl_usd
                            consec_losses = consec_losses + 1 if pnl < 0 else 0
                            in_trade = False
                            trade = None
                            continue
                    else:
                        lookback_start_s = max(0, i - self.trail_bars_short)
                        lookback_s = day_df.iloc[lookback_start_s:i + 1]
                        new_stop = lookback_s['high'].max() + self.trail_delta_short
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
                            daily_pnl_today += trade.pnl_usd
                            consec_losses = consec_losses + 1 if pnl < 0 else 0
                            in_trade = False
                            trade = None
                            continue

                    continue  # still in trade, next bar

                # --- Entree ---
                if trades_today >= self.max_trades_day:
                    continue
                if self.daily_loss_stop > 0 and consec_losses >= self.daily_loss_stop:
                    continue
                if self.daily_loss_usd > 0 and daily_pnl_today <= -self.daily_loss_usd:
                    continue

                close_5 = row['close']
                sma20_5 = row['sma20']
                close_1h = row['close_1h']
                sma20_1h = row['sma20_1h']

                if pd.isna(sma20_5) or pd.isna(sma20_1h):
                    continue

                # Filtre ADX H1 (anti-range)
                if self.adx_threshold > 0:
                    adx_val = row.get('adx_1h', 0)
                    if pd.isna(adx_val) or adx_val < self.adx_threshold:
                        continue

                # Filtre pente SMA20 H1 (pas plate)
                if self.sma_slope_min > 0:
                    slope_val = row.get('sma_slope_1h', 0)
                    if pd.isna(slope_val) or abs(slope_val) < self.sma_slope_min:
                        continue

                # Filtre distance SMA (min et max)
                sma_dist = abs(close_5 - sma20_5)
                if self.min_sma_dist > 0 and sma_dist < self.min_sma_dist:
                    continue
                if self.max_sma_dist > 0 and sma_dist > self.max_sma_dist:
                    continue

                # Filtre ATR minimum
                if self.atr_min > 0:
                    cur_atr = row.get('atr14', 0)
                    if pd.isna(cur_atr) or cur_atr < self.atr_min:
                        continue

                # Filtre distance H1 SMA (tendance assez forte)
                if self.min_h1_sma_dist > 0:
                    h1_dist = abs(close_1h - sma20_1h)
                    if h1_dist < self.min_h1_sma_dist:
                        continue

                direction = None
                if close_5 > sma20_5 and close_1h > sma20_1h:
                    direction = 'long'
                elif close_5 < sma20_5 and close_1h < sma20_1h:
                    direction = 'short'

                # Filtre direction bougie M5 (doit cloturer dans le sens du trade)
                if direction and self.candle_dir_filter:
                    if direction == 'long' and row['close'] <= row['open']:
                        direction = None
                    elif direction == 'short' and row['close'] >= row['open']:
                        direction = None

                # Filtre pullback : le prix doit avoir touche la SMA recemment
                if direction and self.pullback_bars > 0:
                    pb_start = max(0, i - self.pullback_bars)
                    pb_window = day_df.iloc[pb_start:i + 1]
                    pb_sma = pb_window['sma20']
                    if direction == 'long':
                        # Le low d'au moins une bougie a touche la SMA20 (ou en-dessous)
                        dists = pb_window['low'] - pb_sma
                        pullback_ok = (dists <= self.pullback_dist).any()
                    else:
                        # Le high d'au moins une bougie a touche la SMA20 (ou au-dessus)
                        dists = pb_sma - pb_window['high']
                        pullback_ok = (dists <= self.pullback_dist).any()
                    if not pullback_ok:
                        direction = None

                if direction:
                    # Init trailing stop
                    if direction == 'long':
                        lookback_start = max(0, i - self.trail_bars)
                        lookback = day_df.iloc[lookback_start:i + 1]
                        trail_stop = lookback['low'].min()
                    else:
                        lookback_start = max(0, i - self.trail_bars_short)
                        lookback = day_df.iloc[lookback_start:i + 1]
                        trail_stop = lookback['high'].max() + self.trail_delta_short

                    trade = MM20Trade(
                        date=str(day),
                        direction=direction,
                        entry_price=close_5,
                        entry_time=str(paris_time),
                        stop_price=trail_stop,
                    )
                    in_trade = True
                    trades_today += 1

            # Fin de journee : fermer si encore en position (securite)
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
            logger.warning("MM20 backtest: aucun trade genere")
            return None

        return self._build_report(trades, equity)

    def _build_report(self, trades: List[MM20Trade], equity: List[float]) -> MM20Report:
        pnls = [t.pnl_usd for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_pnl = sum(pnls)
        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0

        # Max drawdown en USD
        eq = np.array(equity)
        peak = np.maximum.accumulate(eq)
        dd = eq - peak
        max_dd = abs(dd.min()) if len(dd) > 0 else 0

        # Sharpe
        if len(pnls) > 1:
            sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252) if np.std(pnls) > 0 else 0, 2)
        else:
            sharpe = 0

        # Daily PnL
        daily = {}
        for t in trades:
            d = t.date
            daily[d] = daily.get(d, 0) + t.pnl_usd

        report = MM20Report(
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
