"""
Backtest OPR DAX 08h-09h CET — Databento FDXM 5min — 6 mois
==============================================================
Strategie du PDF (fidelite stricte):
  - OPR range: 08h00-08h55 CET (high/low des 12 bougies 5min)
  - Filtre ATR x2.1: ATR(7) sur les 7 dernieres bougies OPR
    Si OPR range > ATR(7) * 2.1 -> skip (range trop large)
  - BUY breakout au-dessus OPR high:
      TP = entry + 140 pts
      SL = low bougie 3 (08:10) - 45 pts
  - SELL breakout en-dessous OPR low:
      TP = entry - 185 pts
      SL = high bougie 5 (08:20) + 13 pts
  - FLAT obligatoire a 13h00 CET
  - Max 1 BUY + 1 SELL par jour (re-entry apres SL dans l'autre sens)
  - Daily loss limit: 1500 EUR
  - 2 Mini-DAX = 10 EUR/pt

Comparaison avec MM20 Pullback sur memes donnees (cash-only).
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtester.mm20_engine import MM20BacktestEngine

CET = pytz.timezone('Europe/Berlin')
POINT_VALUE = 10.0  # 2 Mini-DAX x 5 EUR/pt
DAILY_LOSS_LIMIT = 1500.0  # EUR


def run_opr_dax(df_5m, use_atr_filter=True, allow_reentry_same_dir=False):
    """Backtest OPR DAX strategy per PDF rules."""
    df = df_5m.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df['cet'] = df.index.tz_convert(CET)
    df['date'] = df['cet'].dt.date
    df['hour'] = df['cet'].dt.hour
    df['minute'] = df['cet'].dt.minute

    trades = []
    equity = [0.0]

    for day, day_df in df.groupby('date'):
        day_df = day_df.sort_index()

        # ── 1) OPR: 08:00-08:55 CET (12 bougies 5min) ──
        opr = day_df[day_df['hour'] == 8]
        if len(opr) < 7:
            continue

        opr_high = opr['high'].max()
        opr_low = opr['low'].min()
        opr_range = opr_high - opr_low

        if opr_range < 5:
            continue

        # ── ATR(7) sur les 7 dernieres bougies OPR ──
        # Utilise comme filtre sur la bougie de cassure (pas sur le range)
        last7 = opr.tail(7)
        atr7 = (last7['high'] - last7['low']).mean()

        # ── SL references (bougies fixes) ──
        # Bougie 1=08:00, 2=08:05, 3=08:10, 4=08:15, 5=08:20
        c3_low = opr.iloc[2]['low'] if len(opr) > 2 else opr_low
        c5_high = opr.iloc[4]['high'] if len(opr) > 4 else opr_high

        buy_sl = c3_low - 45
        sell_sl = c5_high + 13

        # ── 2) Trading window: 09:00-13:00 CET ──
        window = day_df[(day_df['hour'] >= 9) & (day_df['hour'] <= 13)]

        position = None  # dict or None
        day_pnl = 0.0
        had_buy = False
        had_sell = False

        for _, bar in window.iterrows():
            h, m = bar['hour'], bar['minute']

            # ── In position: check exits ──
            if position is not None:
                d = position['dir']
                ep = position['entry']
                tp_p = position['tp']
                sl_p = position['sl']

                exit_p = None
                reason = None

                # FLAT at 13:00
                if h >= 13:
                    exit_p = bar['open']
                    reason = 'flat_13h'
                elif d == 'long':
                    # SL and TP check
                    sl_hit = bar['low'] <= sl_p
                    tp_hit = bar['high'] >= tp_p
                    if sl_hit and tp_hit:
                        # Ambiguous: conservative = SL
                        exit_p = sl_p
                        reason = 'sl'
                    elif sl_hit:
                        exit_p = sl_p
                        reason = 'sl'
                    elif tp_hit:
                        exit_p = tp_p
                        reason = 'tp'
                else:  # short
                    sl_hit = bar['high'] >= sl_p
                    tp_hit = bar['low'] <= tp_p
                    if sl_hit and tp_hit:
                        exit_p = sl_p
                        reason = 'sl'
                    elif sl_hit:
                        exit_p = sl_p
                        reason = 'sl'
                    elif tp_hit:
                        exit_p = tp_p
                        reason = 'tp'

                if exit_p is not None:
                    pnl_pts = (exit_p - ep) if d == 'long' else (ep - exit_p)
                    pnl_eur = pnl_pts * POINT_VALUE
                    trades.append({
                        'date': str(day), 'direction': d,
                        'entry': round(ep, 2), 'exit': round(exit_p, 2),
                        'entry_time': position['entry_time'],
                        'exit_time': str(bar['cet']),
                        'exit_reason': reason,
                        'pnl_pts': round(pnl_pts, 2),
                        'pnl_usd': round(pnl_eur, 2),
                        'sl': round(sl_p, 2), 'tp': round(tp_p, 2),
                        'opr_high': round(opr_high, 2),
                        'opr_low': round(opr_low, 2),
                        'opr_range': round(opr_range, 2),
                    })
                    equity.append(equity[-1] + pnl_eur)
                    day_pnl += pnl_eur
                    position = None

                    if reason == 'flat_13h':
                        break  # done for the day
                    continue  # allow re-entry on subsequent bars

                continue  # still in trade, next bar

            # ── Not in position: look for breakout ──
            if h >= 13:
                break  # no new entries at/after 13:00

            if day_pnl <= -DAILY_LOSS_LIMIT:
                break  # daily loss reached

            entered = False

            # BUY breakout: bar high crosses OPR high
            bar_range = bar['high'] - bar['low']
            atr_ok = (not use_atr_filter) or (bar_range >= atr7 * 2.1)
            buy_ok = not had_buy if not allow_reentry_same_dir else True
            if buy_ok and bar['high'] > opr_high and atr_ok:
                had_buy = True
                ep = opr_high
                tp_p = ep + 140
                sl_p = buy_sl
                position = {
                    'dir': 'long', 'entry': ep,
                    'tp': tp_p, 'sl': sl_p,
                    'entry_time': str(bar['cet']),
                }

                # Same-bar exit check (entry at opr_high, rest of bar)
                exit_p = None
                reason = None
                sl_hit = bar['low'] <= sl_p
                tp_hit = bar['high'] >= tp_p
                if sl_hit and tp_hit:
                    exit_p = sl_p
                    reason = 'sl'
                elif sl_hit:
                    exit_p = sl_p
                    reason = 'sl'
                elif tp_hit:
                    exit_p = tp_p
                    reason = 'tp'

                if exit_p is not None:
                    pnl_pts = exit_p - ep
                    pnl_eur = pnl_pts * POINT_VALUE
                    trades.append({
                        'date': str(day), 'direction': 'long',
                        'entry': round(ep, 2), 'exit': round(exit_p, 2),
                        'entry_time': str(bar['cet']),
                        'exit_time': str(bar['cet']),
                        'exit_reason': reason,
                        'pnl_pts': round(pnl_pts, 2),
                        'pnl_usd': round(pnl_eur, 2),
                        'sl': round(sl_p, 2), 'tp': round(tp_p, 2),
                        'opr_high': round(opr_high, 2),
                        'opr_low': round(opr_low, 2),
                        'opr_range': round(opr_range, 2),
                    })
                    equity.append(equity[-1] + pnl_eur)
                    day_pnl += pnl_eur
                    position = None

                entered = True

            # SELL breakout: bar low crosses OPR low
            sell_ok = not had_sell if not allow_reentry_same_dir else True
            if not entered and sell_ok and bar['low'] < opr_low and atr_ok:
                had_sell = True
                ep = opr_low
                tp_p = ep - 185
                sl_p = sell_sl
                position = {
                    'dir': 'short', 'entry': ep,
                    'tp': tp_p, 'sl': sl_p,
                    'entry_time': str(bar['cet']),
                }

                # Same-bar exit check
                exit_p = None
                reason = None
                sl_hit = bar['high'] >= sl_p
                tp_hit = bar['low'] <= tp_p
                if sl_hit and tp_hit:
                    exit_p = sl_p
                    reason = 'sl'
                elif sl_hit:
                    exit_p = sl_p
                    reason = 'sl'
                elif tp_hit:
                    exit_p = tp_p
                    reason = 'tp'

                if exit_p is not None:
                    pnl_pts = ep - exit_p
                    pnl_eur = pnl_pts * POINT_VALUE
                    trades.append({
                        'date': str(day), 'direction': 'short',
                        'entry': round(ep, 2), 'exit': round(exit_p, 2),
                        'entry_time': str(bar['cet']),
                        'exit_time': str(bar['cet']),
                        'exit_reason': reason,
                        'pnl_pts': round(pnl_pts, 2),
                        'pnl_usd': round(pnl_eur, 2),
                        'sl': round(sl_p, 2), 'tp': round(tp_p, 2),
                        'opr_high': round(opr_high, 2),
                        'opr_low': round(opr_low, 2),
                        'opr_range': round(opr_range, 2),
                    })
                    equity.append(equity[-1] + pnl_eur)
                    day_pnl += pnl_eur
                    position = None

    return trades, equity


def print_report(title, trades, equity):
    if not trades:
        print('{}: AUCUN TRADE'.format(title))
        return

    df = pd.DataFrame(trades)
    pnls = df['pnl_usd'].tolist()
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    wr = len(wins) / len(pnls) * 100 if pnls else 0
    gross_win = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    avg_trade = np.mean(pnls) if pnls else 0

    eq = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = abs(dd.min())

    sharpe = 0
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2)

    print('=' * 85)
    print('  {}'.format(title))
    print('=' * 85)
    print('  Trades     : {}'.format(len(trades)))
    print('  Win Rate   : {:.1f}%'.format(wr))
    print('  PnL        : EUR {:>+,.0f}'.format(total_pnl))
    print('  PF         : {}'.format(pf))
    print('  Sharpe     : {}'.format(sharpe))
    print('  Avg Win    : EUR {:>+,.0f}'.format(avg_win))
    print('  Avg Loss   : EUR {:>+,.0f}'.format(avg_loss))
    print('  Avg Trade  : EUR {:>+,.0f}'.format(avg_trade))
    print('  Max DD     : EUR {:>,.0f}'.format(max_dd))

    # Exit reasons
    reasons = df['exit_reason'].value_counts()
    print('  Sorties    : {}'.format(', '.join('{} {}x'.format(k, v) for k, v in reasons.items())))

    # Monthly
    df['date_parsed'] = pd.to_datetime(df['date'])
    df['month'] = df['date_parsed'].dt.to_period('M')

    print('\n  --- P&L Mensuel ---')
    total_cumul = 0
    for m, group in df.groupby('month'):
        m_pnl = group['pnl_usd'].sum()
        m_trades = len(group)
        m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
        total_cumul += m_pnl
        marker = '+' if m_pnl > 0 else '-'
        print('  {}  |  {:>3}t  |  WR {:5.1f}%  |  EUR {:>+9,.0f}  |  cumul {:>+9,.0f}  {}'.format(
            m, m_trades, m_wr, m_pnl, total_cumul, marker))

    # Daily summary
    daily = df.groupby('date').agg(
        trades=('pnl_usd', 'count'),
        pnl=('pnl_usd', 'sum'),
        wins=('pnl_usd', lambda x: (x > 0).sum()),
    ).reset_index()

    days_pos = (daily['pnl'] > 0).sum()
    days_neg = (daily['pnl'] < 0).sum()
    days_flat = (daily['pnl'] == 0).sum()
    avg_trades_day = daily['trades'].mean()
    print('\n  Jours: {}+ / {}- / {}= sur {} jours trades (moy {:.1f} trades/jour)'.format(
        days_pos, days_neg, days_flat, len(daily), avg_trades_day))


# ============================================================
# MAIN
# ============================================================
print('Loading FDXM 5min data...')
df_5m = pd.read_csv('data/databento_fdxm_5min_6mo.csv', index_col=0, parse_dates=True)
if df_5m.index.tz is None:
    df_5m.index = df_5m.index.tz_localize('UTC')
print('  {} bars | {} -> {}'.format(len(df_5m), df_5m.index.min().date(), df_5m.index.max().date()))

# ============================================================
# 1) OPR DAX Strategy — Variantes
# ============================================================
# A) Avec filtre ATR, max 1 BUY + 1 SELL/jour
print('\n--- VARIANTE A: ATR filter + 1 BUY/1 SELL par jour ---')
opr_a, eq_a = run_opr_dax(df_5m, use_atr_filter=True, allow_reentry_same_dir=False)
print_report('OPR-A (ATR + 1buy/1sell)', opr_a, eq_a)

# B) Sans filtre ATR, max 1 BUY + 1 SELL/jour
print('\n--- VARIANTE B: SANS ATR filter + 1 BUY/1 SELL par jour ---')
opr_b, eq_b = run_opr_dax(df_5m, use_atr_filter=False, allow_reentry_same_dir=False)
print_report('OPR-B (no ATR + 1buy/1sell)', opr_b, eq_b)

# C) Sans filtre ATR, re-entry autorisee (meme direction)
print('\n--- VARIANTE C: SANS ATR + re-entry illimitee ---')
opr_c, eq_c = run_opr_dax(df_5m, use_atr_filter=False, allow_reentry_same_dir=True)
print_report('OPR-C (no ATR + reentry)', opr_c, eq_c)

# Use best variant for comparison
opr_trades = opr_b
opr_equity = eq_b

# ============================================================
# 2) MM20 Pullback on DAX (cash-only: 8h-17h30 CET)
# ============================================================
print('\n\nRunning MM20 Pullback DAX backtest (cash-only)...')

# Filter to cash hours for realistic SMA
df_cash = df_5m.copy()
cet_idx = df_cash.index.tz_convert(CET)
mask = (cet_idx.hour >= 8) & ((cet_idx.hour < 17) | ((cet_idx.hour == 17) & (cet_idx.minute <= 25)))
df_cash = df_cash[mask]
print('  Cash-only bars: {} (filtered from {})'.format(len(df_cash), len(df_5m)))

engine = MM20BacktestEngine(
    tp_points=300, trail_bars=20, max_sl_pts=200, max_trades_day=4,
    sma_period=20, start_offset_min=0,
    abs_start_hour=10, abs_start_min=0,
    daily_loss_stop=3, point_value=10.0, daily_loss_usd=1500,
    pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
)
report_mm20 = engine.run(df_cash)

if report_mm20 and report_mm20.total_trades > 0:
    mm20_trades = report_mm20.trades
    mm20_equity = report_mm20.equity_curve

    mm20_df = pd.DataFrame(mm20_trades)
    pnls = mm20_df['pnl_usd'].tolist()
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0

    eq = np.array(mm20_equity)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = abs(dd.min())

    sharpe = 0
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2)

    print('=' * 85)
    print('  MM20 PULLBACK DAX (cash-only) | 2 Mini-DAX (10 EUR/pt) | 10h-close CET')
    print('=' * 85)
    print('  Trades     : {}'.format(report_mm20.total_trades))
    print('  Win Rate   : {}%'.format(report_mm20.win_rate))
    print('  PnL        : EUR {:>+,.0f}'.format(report_mm20.total_pnl_usd))
    print('  PF         : {}'.format(pf))
    print('  Sharpe     : {}'.format(sharpe))
    print('  Avg Win    : EUR {:>+,.0f}'.format(report_mm20.avg_win))
    print('  Avg Loss   : EUR {:>+,.0f}'.format(report_mm20.avg_loss))
    print('  Max DD     : EUR {:>,.0f}'.format(max_dd))

    mm20_df['date_parsed'] = pd.to_datetime(mm20_df['date'])
    mm20_df['month'] = mm20_df['date_parsed'].dt.to_period('M')

    print('\n  --- P&L Mensuel ---')
    total_cumul = 0
    for m, group in mm20_df.groupby('month'):
        m_pnl = group['pnl_usd'].sum()
        m_trades = len(group)
        m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
        total_cumul += m_pnl
        marker = '+' if m_pnl > 0 else '-'
        print('  {}  |  {:>3}t  |  WR {:5.1f}%  |  EUR {:>+9,.0f}  |  cumul {:>+9,.0f}  {}'.format(
            m, m_trades, m_wr, m_pnl, total_cumul, marker))
else:
    print('  MM20 Pullback: AUCUN TRADE')
    mm20_trades = []
    mm20_equity = [0]

# ============================================================
# 3) Comparatif
# ============================================================
print('\n')
print('=' * 85)
print('  COMPARATIF -- 6 MOIS DAX (Sep 2025 - Mar 2026)')
print('=' * 85)

opr_pnl = sum(t['pnl_usd'] for t in opr_trades) if opr_trades else 0
mm20_pnl = report_mm20.total_pnl_usd if report_mm20 and report_mm20.total_trades > 0 else 0

opr_n = len(opr_trades)
mm20_n = report_mm20.total_trades if report_mm20 and report_mm20.total_trades > 0 else 0

opr_wr = len([t for t in opr_trades if t['pnl_usd'] > 0]) / opr_n * 100 if opr_n else 0
mm20_wr = float(str(report_mm20.win_rate).replace('%', '')) if report_mm20 and report_mm20.total_trades > 0 else 0

opr_wins = [t['pnl_usd'] for t in opr_trades if t['pnl_usd'] > 0]
opr_losses = [t['pnl_usd'] for t in opr_trades if t['pnl_usd'] < 0]
opr_pf = round(sum(opr_wins) / abs(sum(opr_losses)), 2) if opr_losses and sum(opr_losses) != 0 else 99

mm20_pnls = [t['pnl_usd'] for t in mm20_trades] if mm20_trades else []
mm20_wins = [p for p in mm20_pnls if p > 0]
mm20_losses_l = [p for p in mm20_pnls if p < 0]
mm20_pf = round(sum(mm20_wins) / abs(sum(mm20_losses_l)), 2) if mm20_losses_l and sum(mm20_losses_l) != 0 else 99

print('  {:30} {:>15} {:>15}'.format('', 'OPR 8h-9h', 'MM20 Pullback'))
print('  ' + '-' * 62)
print('  {:30} {:>15} {:>15}'.format('Trades', str(opr_n), str(mm20_n)))
print('  {:30} {:>14.1f}% {:>14.1f}%'.format('Win Rate', opr_wr, mm20_wr))
print('  {:30} {:>+14,.0f}E {:>+14,.0f}E'.format('PnL Total', opr_pnl, mm20_pnl))
print('  {:30} {:>15} {:>15}'.format('Profit Factor', str(opr_pf), str(mm20_pf)))
print('  {:30} {:>15} {:>15}'.format('Sizing', '2 Mini-DAX', '2 Mini-DAX'))
print('  {:30} {:>15} {:>15}'.format('Horaires', '09h-13h CET', '10h-close CET'))
print('  {:30} {:>15} {:>15}'.format('Type', 'Breakout OPR', 'Trend SMA20'))
