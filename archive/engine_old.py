"""
Backtester Engine — Dalton Market Profile
Methodology: Use PREVIOUS day's VA (VAH/VAL/VPOC) as reference levels.
- Fake Breakout: price probes beyond yesterday's VA then reverses (ranging regime)
- Real Breakout: price breaks and holds beyond yesterday's VA (trending regime)
Regime classification: 3-indicator scoring (range_ratio, adx_proxy, vpoc_position)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field, asdict
from typing import Optional, List
from loguru import logger

POINT_VALUE = 20.0  # 1 point NQ = $20


@dataclass
class BacktestTrade:
    entry_time: str
    exit_time: str
    direction: str          # long / short
    entry_price: float
    exit_price: float
    stop_price: float
    pnl_points: float
    pnl_dollars: float
    exit_reason: str        # stop/mm20/vpoc/target/session_end
    strategy: str           # fake_breakout / breakout
    day_type: str
    profile_shape: str
    options_bias: str
    confidence: float
    bars_held: int


@dataclass
class BacktestReport:
    # Global
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_dollars: float = 0.0
    total_pnl_points: float = 0.0

    # Quality
    avg_win_dollars: float = 0.0
    avg_loss_dollars: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    # Per strategy
    fake_breakout_trades: int = 0
    fake_breakout_winrate: float = 0.0
    breakout_trades: int = 0
    breakout_winrate: float = 0.0

    # Options filter
    trades_with_options: int = 0
    winrate_with_options: float = 0.0
    trades_without_options: int = 0
    winrate_without_options: float = 0.0

    # Exits
    exits_stop: int = 0
    exits_mm20: int = 0
    exits_vpoc: int = 0
    exits_target: int = 0
    exits_session: int = 0
    exits_daily_limit: int = 0

    # Improvement metrics
    pauses_triggered: int = 0
    adx_filters: int = 0
    mid_stops_hit: int = 0
    consecutive_stops_max: int = 0

    # Curves
    equity_curve: list = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)


class BacktestEngine:

    # Regime classification thresholds (calibrated on 120 days real NQ)
    REGIME_THRESHOLDS = {
        'range_ratio_low': 0.467,
        'range_ratio_high': 0.533,
        'adx_proxy_low': 0.283,
        'adx_proxy_high': 0.580,
        'vpoc_extreme': 0.35,
    }

    def __init__(self, params: dict = None):
        self.trades: List[BacktestTrade] = []
        self.equity = 0.0
        self.equity_curve = [0.0]
        self.daily_pnl = {}

        p = params or {}
        self.stop_fb = p.get('stop_fb', 30.0)
        self.stop_br = p.get('stop_br', 20.0)
        self.trail_fb = p.get('trail_fb', 0.0)    # 0 = fixed stop for FB
        self.trail_br = p.get('trail_br', 15.0)
        self.exit_fb = p.get('exit_fb', 'vpoc')

        # Improvement 1: Pause after consecutive stops
        self.consecutive_stops = 0
        self.pause_until_bar = 0
        self._pauses_triggered = 0
        self._consecutive_stops_max = 0

        # Improvement 2: ADX filter tracking
        self._adx_filters = 0

        # Improvement 4: Mid-stop tracking
        self.mid_stop_set = False
        self._mid_stops_hit = 0

    # ------------------------------------------------------------------
    # Build daily profile from a full day's data (end-of-day)
    # ------------------------------------------------------------------
    def _build_daily_profile(self, day_df: pd.DataFrame) -> dict:
        """Compute VA, VPOC, and regime for a complete trading day."""
        closes = day_df['close'].values
        volumes = day_df['volume'].values
        highs = day_df['high'].values
        lows = day_df['low'].values

        day_high = float(highs.max())
        day_low = float(lows.min())
        day_range = day_high - day_low
        if day_range < 1 or len(closes) < 30:
            return {}

        # Volume profile
        rounded = np.round(closes * 4) / 4
        unique_prices, inverse = np.unique(rounded, return_inverse=True)
        vol_at_price = np.zeros(len(unique_prices))
        np.add.at(vol_at_price, inverse, volumes)

        if len(unique_prices) == 0:
            return {}

        vpoc_idx = np.argmax(vol_at_price)
        vpoc = float(unique_prices[vpoc_idx])

        total_vol = vol_at_price.sum()
        target_vol = total_vol * 0.70

        sorted_idx = np.argsort(vol_at_price)[::-1]
        cumvol = 0
        va_indices = []
        for idx in sorted_idx:
            cumvol += vol_at_price[idx]
            va_indices.append(idx)
            if cumvol >= target_vol:
                break

        va_prices = unique_prices[va_indices]
        vah = float(va_prices.max())
        val = float(va_prices.min())
        va_width = vah - val

        # Regime classification
        range_ratio = va_width / day_range
        adx_proxy = abs(float(closes[-1]) - float(closes[0])) / day_range
        vpoc_position = (vpoc - day_low) / day_range

        th = self.REGIME_THRESHOLDS
        score_t, score_r = 0, 0

        if range_ratio < th['range_ratio_low']:
            score_t += 1
        elif range_ratio > th['range_ratio_high']:
            score_r += 1

        if adx_proxy > th['adx_proxy_high']:
            score_t += 1
        elif adx_proxy < th['adx_proxy_low']:
            score_r += 1

        if vpoc_position > 0.65 or vpoc_position < th['vpoc_extreme']:
            score_t += 1
        elif 0.40 < vpoc_position < 0.60:
            score_r += 1

        if score_t >= 2:
            regime = 'trending'
        elif score_r >= 2:
            regime = 'ranging'
        else:
            regime = 'mixed'

        return {
            'vpoc': vpoc, 'vah': vah, 'val': val,
            'day_high': day_high, 'day_low': day_low,
            'regime': regime,
            'range_ratio': round(range_ratio, 3),
            'adx_proxy': round(adx_proxy, 3),
        }

    # ------------------------------------------------------------------
    # MM20 on 5-min bars
    # ------------------------------------------------------------------
    def _mm20_5min(self, df_5min: pd.DataFrame, current_time) -> Optional[float]:
        try:
            mask = df_5min.index <= current_time
            sub = df_5min[mask].tail(25)
            if len(sub) < 20:
                return None
            return float(sub['close'].rolling(20).mean().iloc[-1])
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Signal detection using PREVIOUS day's VA levels
    # ------------------------------------------------------------------
    def _check_fake_breakout(
        self, df: pd.DataFrame, idx: int, prev_vah: float, prev_val: float
    ) -> Optional[str]:
        """Detect fake breakout against previous day's VA levels."""
        if idx < 5:
            return None

        bar = df.iloc[idx]
        p1 = df.iloc[idx - 1]
        p2 = df.iloc[idx - 2]
        p3 = df.iloc[idx - 3]

        # Fake breakout UP: wicks probe above prev VAH, close falls back
        # + Improvement 3: confirmation bar closes below prev bar's close
        max_probe = max(p1['high'], p2['high'], p3['high'])
        probe_depth = max_probe - prev_vah
        if (probe_depth >= 5.0 and
            p2['high'] > prev_vah and p1['high'] > prev_vah and
            bar['close'] < prev_vah and bar['close'] > prev_val and
            bar['close'] < p1['close']):  # confirmation: close < prev close
            bar_range = bar['high'] - bar['low']
            if bar_range > 0 and (bar['close'] - bar['low']) / bar_range < 0.5:
                return 'short'

        # Fake breakout DOWN: wicks probe below prev VAL, close rises back
        # + Improvement 3: confirmation bar closes above prev bar's close
        min_probe = min(p1['low'], p2['low'], p3['low'])
        probe_depth = prev_val - min_probe
        if (probe_depth >= 5.0 and
            p2['low'] < prev_val and p1['low'] < prev_val and
            bar['close'] > prev_val and bar['close'] < prev_vah and
            bar['close'] > p1['close']):  # confirmation: close > prev close
            bar_range = bar['high'] - bar['low']
            if bar_range > 0 and (bar['high'] - bar['close']) / bar_range < 0.5:
                return 'long'

        return None

    def _check_real_breakout(
        self, df: pd.DataFrame, idx: int, prev_vah: float, prev_val: float
    ) -> Optional[str]:
        """Detect real breakout beyond previous day's VA levels."""
        if idx < 5:
            return None

        bar = df.iloc[idx]
        p1 = df.iloc[idx - 1]
        p2 = df.iloc[idx - 2]
        p3 = df.iloc[idx - 3]
        delta = bar['close'] - bar['open']

        # Breakout UP: 3 consecutive closes above prev VAH + positive delta + depth
        depth = bar['close'] - prev_vah
        if (depth >= 5.0 and
            p3['close'] > prev_vah and p2['close'] > prev_vah and
            p1['close'] > prev_vah and bar['close'] > prev_vah and
            delta > 0):
            return 'long'

        # Breakout DOWN: 3 consecutive closes below prev VAL + negative delta + depth
        depth = prev_val - bar['close']
        if (depth >= 5.0 and
            p3['close'] < prev_val and p2['close'] < prev_val and
            p1['close'] < prev_val and bar['close'] < prev_val and
            delta < 0):
            return 'short'

        return None

    # ------------------------------------------------------------------
    # Core backtest loop
    # ------------------------------------------------------------------
    def run(
        self,
        df_1min: pd.DataFrame,
        df_5min: pd.DataFrame,
        options_levels: dict = None,
        start_hour: int = 9,
        end_hour: int = 21,
        end_minute: int = 30,
        daily_loss_limit: float = 0,
        max_trades_per_day: int = 0,
        reduce_at: float = 0,
    ) -> Optional[BacktestReport]:

        # --- Step 1: Pre-compute daily profiles ---
        df_1min = df_1min.copy()
        df_1min['date'] = df_1min.index.date
        trading_days = sorted(df_1min['date'].unique())

        daily_profiles = {}
        for day in trading_days:
            day_data = df_1min[df_1min['date'] == day]
            profile = self._build_daily_profile(day_data)
            if profile:
                daily_profiles[day] = profile

        logger.info(f"Pre-computed {len(daily_profiles)} daily profiles from {len(trading_days)} days")

        # Precompute MM20 on 5min
        df_5min = df_5min.copy()
        df_5min['mm20'] = df_5min['close'].rolling(20).mean()

        # --- Step 2: Main loop using previous day's VA ---
        in_trade = False
        direction = ''
        entry_price = 0.0
        stop_price = 0.0
        entry_time = None
        strategy_name = ''
        day_type_str = ''
        profile_str = ''
        options_bias = 'none'
        confidence = 0.0
        bars_held = 0

        current_date = None
        trades_today = 0
        daily_pnl_real = 0.0
        session_start_idx = 0
        prev_profile = None  # Yesterday's profile
        today_regime = 'mixed'
        ib_complete = False
        session_adx = None  # Improvement 2

        total_bars = len(df_1min)
        logger.info(f"Backtest: {total_bars} barres 1min, {len(df_5min)} barres 5min")

        for i in range(30, total_bars):
            bar = df_1min.iloc[i]
            current_time = df_1min.index[i]

            try:
                hour = current_time.hour
                minute = current_time.minute
                bar_date = current_time.date()
                ct = current_time
            except Exception:
                continue

            # New day reset
            if bar_date != current_date:
                # Find previous trading day's profile
                if current_date is not None and current_date in daily_profiles:
                    prev_profile = daily_profiles[current_date]
                current_date = bar_date
                trades_today = 0
                daily_pnl_real = 0.0
                session_start_idx = i
                ib_complete = False
                session_adx = None
                adx_override = False  # True if ADX overrode regime today
                self.consecutive_stops = 0

                # Today's regime comes from developing session (use prev day as fallback)
                if bar_date in daily_profiles:
                    today_regime = daily_profiles[bar_date].get('regime', 'mixed')
                elif prev_profile:
                    today_regime = prev_profile.get('regime', 'mixed')
                else:
                    today_regime = 'mixed'

            # Mark IB complete after 60 bars
            if not ib_complete and i - session_start_idx >= 60:
                ib_complete = True

            # Improvement 2: Calculate session ADX after 20 bars
            bars_in_session = i - session_start_idx
            if bars_in_session == 20 and session_adx is None:
                window = df_1min.iloc[session_start_idx:i]
                w_highs = window['high'].values
                w_lows = window['low'].values
                w_closes = window['close'].values
                tr_list = []
                dm_plus = []
                dm_minus = []
                for k in range(1, len(w_highs)):
                    tr = max(
                        w_highs[k] - w_lows[k],
                        abs(w_highs[k] - w_closes[k - 1]),
                        abs(w_lows[k] - w_closes[k - 1])
                    )
                    tr_list.append(tr)
                    up = w_highs[k] - w_highs[k - 1]
                    down = w_lows[k - 1] - w_lows[k]
                    dm_plus.append(up if up > down and up > 0 else 0)
                    dm_minus.append(down if down > up and down > 0 else 0)
                if tr_list:
                    atr14 = np.mean(tr_list)
                    dip = np.mean(dm_plus) / max(atr14, 0.01) * 100
                    dim = np.mean(dm_minus) / max(atr14, 0.01) * 100
                    dx = abs(dip - dim) / max(dip + dim, 0.01) * 100
                    session_adx = dx
                    # Improvement 2: Override regime once if ADX very strong
                    if session_adx > 50 and today_regime != 'trending':
                        today_regime = 'trending'
                        adx_override = True
                        self._adx_filters += 1

            # Hour filter
            time_minutes = hour * 60 + minute
            if time_minutes < start_hour * 60 or time_minutes >= end_hour * 60 + end_minute:
                if in_trade:
                    pnl_pts = self._pnl(entry_price, bar['close'], direction)
                    self._record(
                        entry_time, ct, direction, entry_price, bar['close'],
                        stop_price, pnl_pts, 'session_end', strategy_name,
                        day_type_str, profile_str, options_bias, confidence, bars_held
                    )
                    in_trade = False
                    trades_today += 1
                    daily_pnl_real += pnl_pts * POINT_VALUE
                continue

            # Topstep daily limit
            if daily_loss_limit < 0 and daily_pnl_real <= daily_loss_limit:
                if in_trade:
                    pnl_pts = self._pnl(entry_price, bar['close'], direction)
                    self._record(
                        entry_time, ct, direction, entry_price, bar['close'],
                        stop_price, pnl_pts, 'daily_limit', strategy_name,
                        day_type_str, profile_str, options_bias, confidence, bars_held
                    )
                    daily_pnl_real += pnl_pts * POINT_VALUE
                    in_trade = False
                    trades_today += 1
                continue

            # No previous day profile = skip (first day)
            if prev_profile is None:
                continue

            prev_vah = prev_profile['vah']
            prev_val = prev_profile['val']
            prev_vpoc = prev_profile['vpoc']

            # Options bias
            options_bias = 'none'
            options_conf_bonus = 0.0
            if options_levels:
                hvl = options_levels.get('hvl')
                if hvl:
                    if bar['close'] < hvl:
                        options_bias = 'negative_gamma'
                        options_conf_bonus = 0.10
                    else:
                        options_bias = 'positive_gamma'
                        options_conf_bonus = 0.10

            # === MANAGE OPEN TRADE ===
            if in_trade:
                bars_held += 1
                high = bar['high']
                low = bar['low']

                # Per-strategy trailing
                trail = self.trail_fb if strategy_name == 'fake_breakout' else self.trail_br
                if trail > 0:
                    if direction == 'long':
                        new_stop = low - trail
                        if new_stop > stop_price:
                            stop_price = new_stop
                    else:
                        new_stop = high + trail
                        if new_stop < stop_price:
                            stop_price = new_stop

                # Improvement 4: Mid-stop (breakeven) for fake_breakout
                if strategy_name == 'fake_breakout' and not self.mid_stop_set:
                    mid_target = (entry_price + (prev_vpoc - entry_price) * 0.5
                                  if direction == 'long'
                                  else entry_price - (entry_price - prev_vpoc) * 0.5)
                    mid_reached = ((direction == 'long' and bar['close'] >= mid_target) or
                                   (direction == 'short' and bar['close'] <= mid_target))
                    if mid_reached:
                        if direction == 'long':
                            new_stop = entry_price + 2
                            if new_stop > stop_price:
                                stop_price = new_stop
                        else:
                            new_stop = entry_price - 2
                            if new_stop < stop_price:
                                stop_price = new_stop
                        self.mid_stop_set = True
                        self._mid_stops_hit += 1

                # Check stop hit
                stop_hit = (direction == 'long' and low <= stop_price) or \
                           (direction == 'short' and high >= stop_price)
                if stop_hit:
                    pnl_pts = self._pnl(entry_price, stop_price, direction)
                    self._record(
                        entry_time, ct, direction, entry_price, stop_price,
                        stop_price, pnl_pts, 'stop', strategy_name,
                        day_type_str, profile_str, options_bias, confidence, bars_held
                    )
                    in_trade = False
                    trades_today += 1
                    daily_pnl_real += pnl_pts * POINT_VALUE
                    # Improvement 1: track consecutive stops
                    self.consecutive_stops += 1
                    if self.consecutive_stops > self._consecutive_stops_max:
                        self._consecutive_stops_max = self.consecutive_stops
                    if self.consecutive_stops >= 2:
                        self.pause_until_bar = i + 30
                        self._pauses_triggered += 1
                        logger.debug(f'Pause 30min after {self.consecutive_stops} consecutive stops')
                    continue

                # Exit logic
                exited = False

                if strategy_name == 'fake_breakout':
                    exit_mode = self.exit_fb

                    # VPOC exit (target = previous day VPOC)
                    if exit_mode in ('vpoc', 'both'):
                        reached = (direction == 'long' and bar['close'] >= prev_vpoc) or \
                                  (direction == 'short' and bar['close'] <= prev_vpoc)
                        if reached:
                            pnl_pts = self._pnl(entry_price, prev_vpoc, direction)
                            self._record(
                                entry_time, ct, direction, entry_price, prev_vpoc,
                                stop_price, pnl_pts, 'vpoc', strategy_name,
                                day_type_str, profile_str, options_bias, confidence, bars_held
                            )
                            in_trade = False
                            trades_today += 1
                            daily_pnl_real += pnl_pts * POINT_VALUE
                            self.consecutive_stops = 0  # Reset on win
                            exited = True

                    # MM20 exit
                    if not exited and exit_mode in ('mm20', 'both'):
                        mm20 = self._mm20_5min(df_5min, current_time)
                        if mm20:
                            broken = (direction == 'long' and bar['close'] < mm20) or \
                                     (direction == 'short' and bar['close'] > mm20)
                            if broken:
                                pnl_pts = self._pnl(entry_price, bar['close'], direction)
                                self._record(
                                    entry_time, ct, direction, entry_price, bar['close'],
                                    stop_price, pnl_pts, 'mm20', strategy_name,
                                    day_type_str, profile_str, options_bias, confidence, bars_held
                                )
                                in_trade = False
                                trades_today += 1
                                daily_pnl_real += pnl_pts * POINT_VALUE
                                exited = True

                    # Fixed target
                    if not exited and exit_mode.startswith('fixed_'):
                        target_pts = float(exit_mode.split('_')[1])
                        target = entry_price + target_pts if direction == 'long' else entry_price - target_pts
                        reached = (direction == 'long' and bar['close'] >= target) or \
                                  (direction == 'short' and bar['close'] <= target)
                        if reached:
                            pnl_pts = self._pnl(entry_price, target, direction)
                            self._record(
                                entry_time, ct, direction, entry_price, target,
                                stop_price, pnl_pts, 'target', strategy_name,
                                day_type_str, profile_str, options_bias, confidence, bars_held
                            )
                            in_trade = False
                            trades_today += 1
                            daily_pnl_real += pnl_pts * POINT_VALUE
                            exited = True

                    if exited:
                        continue

                # Breakout exit: MM20 5min
                else:
                    mm20 = self._mm20_5min(df_5min, current_time)
                    if mm20:
                        broken = (direction == 'long' and bar['close'] < mm20) or \
                                 (direction == 'short' and bar['close'] > mm20)
                        if broken:
                            pnl_pts = self._pnl(entry_price, bar['close'], direction)
                            self._record(
                                entry_time, ct, direction, entry_price, bar['close'],
                                stop_price, pnl_pts, 'mm20', strategy_name,
                                day_type_str, profile_str, options_bias, confidence, bars_held
                            )
                            in_trade = False
                            trades_today += 1
                            daily_pnl_real += pnl_pts * POINT_VALUE
                            continue

            # === SEARCH FOR ENTRY SIGNAL ===
            else:
                # Wait for IB to complete
                if not ib_complete:
                    continue

                # Improvement 1: Pause after consecutive stops
                if i < self.pause_until_bar:
                    continue

                max_t = max_trades_per_day if max_trades_per_day > 0 else 2
                if trades_today >= max_t:
                    continue
                loss_lim = daily_loss_limit if daily_loss_limit < 0 else -500
                if daily_pnl_real <= loss_lim:
                    continue

                # Improvement 2: ADX filter — override regime once per day if ADX is strong
                recommended = 'breakout' if today_regime == 'trending' else 'fake_breakout'

                # Use strategy based on regime (possibly overridden by ADX)
                if recommended == 'breakout':
                    sig = self._check_real_breakout(df_1min, i, prev_vah, prev_val)
                    if sig:
                        base_conf = 0.72
                        if options_bias == 'negative_gamma':
                            base_conf += options_conf_bonus
                        in_trade = True
                        direction = sig
                        entry_price = bar['close']
                        entry_time = ct
                        strategy_name = 'breakout'
                        day_type_str = today_regime
                        profile_str = prev_profile.get('regime', 'unknown')
                        confidence = base_conf
                        bars_held = 0
                        self.mid_stop_set = False  # Improvement 4: reset
                        stop_price = (bar['low'] - self.stop_br) if direction == 'long' else (bar['high'] + self.stop_br)

                else:
                    sig = self._check_fake_breakout(df_1min, i, prev_vah, prev_val)
                    if sig:
                        base_conf = 0.78
                        if options_bias == 'positive_gamma':
                            base_conf += options_conf_bonus
                        in_trade = True
                        direction = sig
                        entry_price = bar['close']
                        entry_time = ct
                        strategy_name = 'fake_breakout'
                        day_type_str = today_regime
                        profile_str = prev_profile.get('regime', 'unknown')
                        confidence = base_conf
                        bars_held = 0
                        self.mid_stop_set = False  # Improvement 4: reset
                        stop_price = (bar['low'] - self.stop_fb) if direction == 'long' else (bar['high'] + self.stop_fb)

        # Close remaining trade
        if in_trade and total_bars > 0:
            last_bar = df_1min.iloc[-1]
            last_time = df_1min.index[-1]
            pnl_pts = self._pnl(entry_price, last_bar['close'], direction)
            self._record(
                entry_time, last_time, direction, entry_price, last_bar['close'],
                stop_price, pnl_pts, 'end_of_data', strategy_name,
                day_type_str, profile_str, options_bias, confidence, bars_held
            )

        return self._generate_report()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pnl(self, entry: float, exit_p: float, direction: str) -> float:
        return (exit_p - entry) if direction == 'long' else (entry - exit_p)

    def _record(
        self, entry_time, exit_time, direction, entry, exit_p,
        stop, pnl_pts, reason, strategy, day_type, profile,
        options_bias, confidence, bars_held
    ):
        pnl_usd = pnl_pts * POINT_VALUE
        self.equity += pnl_usd
        self.equity_curve.append(self.equity)

        date_str = str(exit_time.date() if hasattr(exit_time, 'date') else exit_time)[:10]
        self.daily_pnl[date_str] = self.daily_pnl.get(date_str, 0) + pnl_usd

        self.trades.append(BacktestTrade(
            entry_time=str(entry_time),
            exit_time=str(exit_time),
            direction=direction,
            entry_price=round(entry, 2),
            exit_price=round(exit_p, 2),
            stop_price=round(stop, 2),
            pnl_points=round(pnl_pts, 2),
            pnl_dollars=round(pnl_usd, 2),
            exit_reason=reason,
            strategy=strategy,
            day_type=day_type,
            profile_shape=profile,
            options_bias=options_bias,
            confidence=round(confidence, 2),
            bars_held=bars_held,
        ))

    def _generate_report(self) -> Optional[BacktestReport]:
        if not self.trades:
            return None

        trades = self.trades
        pnls = [t.pnl_dollars for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        def wr(lst):
            if not lst:
                return 0.0
            w = [t for t in lst if t.pnl_dollars > 0]
            return round(len(w) / len(lst) * 100, 1)

        fb = [t for t in trades if t.strategy == 'fake_breakout']
        br = [t for t in trades if t.strategy == 'breakout']
        opt = [t for t in trades if t.options_bias != 'none']
        no_opt = [t for t in trades if t.options_bias == 'none']

        # Max drawdown
        eq = self.equity_curve
        max_dd = 0.0
        peak = eq[0]
        for e in eq:
            if e > peak:
                peak = e
            dd = peak - e
            if dd > max_dd:
                max_dd = dd

        # Profit factor
        gross_win = sum(winners) if winners else 0
        gross_loss = abs(sum(losers)) if losers else 0
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else float('inf')

        # Expectancy
        avg_w = np.mean(winners) if winners else 0
        avg_l = abs(np.mean(losers)) if losers else 0
        wr_pct = len(winners) / len(trades)
        lr_pct = len(losers) / len(trades)
        exp = avg_w * wr_pct - avg_l * lr_pct

        # Sharpe
        if len(pnls) > 1:
            sharpe = round(np.mean(pnls) / max(np.std(pnls), 0.01) * np.sqrt(252), 2)
        else:
            sharpe = 0.0

        # Exit counts
        exits = {}
        for t in trades:
            exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1

        return BacktestReport(
            total_trades=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=round(len(winners) / len(trades) * 100, 1),
            total_pnl_dollars=round(sum(pnls), 2),
            total_pnl_points=round(sum(t.pnl_points for t in trades), 2),
            avg_win_dollars=round(avg_w, 2),
            avg_loss_dollars=round(avg_l, 2),
            profit_factor=pf,
            expectancy=round(exp, 2),
            max_drawdown=round(max_dd, 2),
            sharpe_ratio=sharpe,
            fake_breakout_trades=len(fb),
            fake_breakout_winrate=wr(fb),
            breakout_trades=len(br),
            breakout_winrate=wr(br),
            trades_with_options=len(opt),
            winrate_with_options=wr(opt),
            trades_without_options=len(no_opt),
            winrate_without_options=wr(no_opt),
            exits_stop=exits.get('stop', 0),
            exits_mm20=exits.get('mm20', 0),
            exits_vpoc=exits.get('vpoc', 0),
            exits_target=exits.get('target', 0),
            exits_session=exits.get('session_end', 0),
            exits_daily_limit=exits.get('daily_limit', 0),
            pauses_triggered=self._pauses_triggered,
            adx_filters=self._adx_filters,
            mid_stops_hit=self._mid_stops_hit,
            consecutive_stops_max=self._consecutive_stops_max,
            equity_curve=self.equity_curve,
            daily_pnl=self.daily_pnl,
            trades=[asdict(t) for t in trades[-50:]],
        )


# ==================================================================
# OPTIMIZER
# ==================================================================

class BacktestOptimizer:
    """Grid search over parameter combinations."""

    PARAM_GRID = {
        'stop_fb': [20, 25, 30, 40],
        'stop_br': [15, 20, 25, 30],
        'trail_fb': [0],              # Fixed stop for mean-reversion
        'trail_br': [10, 15, 20, 0],  # Trail for breakout (0=fixed)
        'exit_fb': ['vpoc', 'both'],
    }

    def run_optimization(
        self,
        df_1min: pd.DataFrame,
        df_5min: pd.DataFrame,
        options_levels: dict = None,
        min_trades: int = 10,
        progress_callback=None,
    ) -> list:
        import itertools

        combos = list(itertools.product(
            self.PARAM_GRID['stop_fb'],
            self.PARAM_GRID['stop_br'],
            self.PARAM_GRID['trail_fb'],
            self.PARAM_GRID['trail_br'],
            self.PARAM_GRID['exit_fb'],
        ))
        total = len(combos)
        logger.info(f"Optimization: {total} combinations")

        results = []

        for idx, (stop_fb, stop_br, trail_fb, trail_br, exit_fb) in enumerate(combos):
            params = {
                'stop_fb': stop_fb, 'stop_br': stop_br,
                'trail_fb': trail_fb, 'trail_br': trail_br,
                'exit_fb': exit_fb,
            }

            engine = BacktestEngine(params=params)
            report = engine.run(df_1min, df_5min, options_levels)

            if report and report.total_trades >= min_trades:
                pf_safe = min(report.profit_factor, 10.0)
                score = (
                    pf_safe * 0.4 +
                    report.sharpe_ratio * 0.3 +
                    min(report.win_rate / 100, 1.0) * 0.2 -
                    (report.max_drawdown / 10000) * 0.1
                )

                results.append({
                    'params': params,
                    'trades': report.total_trades,
                    'win_rate': report.win_rate,
                    'pnl': report.total_pnl_dollars,
                    'profit_factor': pf_safe,
                    'max_drawdown': report.max_drawdown,
                    'expectancy': report.expectancy,
                    'sharpe': report.sharpe_ratio,
                    'avg_win': report.avg_win_dollars,
                    'avg_loss': report.avg_loss_dollars,
                    'exits_stop': report.exits_stop,
                    'exits_vpoc': report.exits_vpoc,
                    'exits_mm20': report.exits_mm20,
                    'exits_target': report.exits_target,
                    'score': round(score, 4),
                })

            if progress_callback and (idx + 1) % 10 == 0:
                progress_callback(idx + 1, total)

        results.sort(key=lambda x: x['score'], reverse=True)
        logger.info(f"Optimization done: {len(results)}/{total} valid combos")
        return results
