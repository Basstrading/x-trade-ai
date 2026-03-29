"""
Comparaison OPR DAX vs MM20 Pullback DAX — 1 FDAX (25 EUR/pt)
===============================================================
Donnees: Databento FDXM 5min (Mar 2025 - Mar 2026 = 12 mois)
Note: leur backtest couvre Jan 2024 - Jul 2025 (19 mois)
      Databento XEUR.EOBI ne remonte qu'a mars 2025,
      donc on teste sur la periode disponible: 12 mois.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtester.mm20_engine import MM20BacktestEngine

CET = pytz.timezone('Europe/Berlin')
POINT_VALUE_FDAX = 25.0  # 1 FDAX = 25 EUR/pt (comme leur backtest)


def run_opr_dax(df_5m, point_value=25.0):
    """Backtest OPR DAX per PDF rules. 1 FDAX (25 EUR/pt)."""
    df = df_5m.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df['cet'] = df.index.tz_convert(CET)
    df['date'] = df['cet'].dt.date
    df['hour'] = df['cet'].dt.hour
    df['minute'] = df['cet'].dt.minute

    trades = []
    equity = [0.0]
    daily_loss_limit = 1500.0

    for day, day_df in df.groupby('date'):
        day_df = day_df.sort_index()

        # OPR: 08:00-08:55 CET
        opr = day_df[day_df['hour'] == 8]
        if len(opr) < 7:
            continue

        opr_high = opr['high'].max()
        opr_low = opr['low'].min()
        opr_range = opr_high - opr_low

        if opr_range < 5:
            continue

        # ATR(7) sur les 7 dernieres bougies OPR
        last7 = opr.tail(7)
        atr7 = (last7['high'] - last7['low']).mean()

        # SL references
        c3_low = opr.iloc[2]['low'] if len(opr) > 2 else opr_low
        c5_high = opr.iloc[4]['high'] if len(opr) > 4 else opr_high
        buy_sl = c3_low - 45
        sell_sl = c5_high + 13

        # Trading window: 09:00-13:00 CET
        window = day_df[(day_df['hour'] >= 9) & (day_df['hour'] <= 13)]

        position = None
        day_pnl = 0.0
        had_buy = False
        had_sell = False

        for _, bar in window.iterrows():
            h, m = bar['hour'], bar['minute']

            if position is not None:
                d = position['dir']
                ep = position['entry']
                tp_p = position['tp']
                sl_p = position['sl']

                exit_p = None
                reason = None

                if h >= 13:
                    exit_p = bar['open']
                    reason = 'flat_13h'
                elif d == 'long':
                    sl_hit = bar['low'] <= sl_p
                    tp_hit = bar['high'] >= tp_p
                    if sl_hit and tp_hit:
                        exit_p = sl_p; reason = 'sl'
                    elif sl_hit:
                        exit_p = sl_p; reason = 'sl'
                    elif tp_hit:
                        exit_p = tp_p; reason = 'tp'
                else:
                    sl_hit = bar['high'] >= sl_p
                    tp_hit = bar['low'] <= tp_p
                    if sl_hit and tp_hit:
                        exit_p = sl_p; reason = 'sl'
                    elif sl_hit:
                        exit_p = sl_p; reason = 'sl'
                    elif tp_hit:
                        exit_p = tp_p; reason = 'tp'

                if exit_p is not None:
                    pnl_pts = (exit_p - ep) if d == 'long' else (ep - exit_p)
                    pnl_eur = pnl_pts * point_value
                    trades.append({
                        'date': str(day), 'direction': d,
                        'entry': round(ep, 2), 'exit': round(exit_p, 2),
                        'entry_time': position['entry_time'],
                        'exit_time': str(bar['cet']),
                        'exit_reason': reason,
                        'pnl_pts': round(pnl_pts, 2),
                        'pnl_usd': round(pnl_eur, 2),
                    })
                    equity.append(equity[-1] + pnl_eur)
                    day_pnl += pnl_eur
                    position = None
                    if reason == 'flat_13h':
                        break
                    continue
                continue

            if h >= 13:
                break
            if day_pnl <= -daily_loss_limit:
                break

            entered = False
            bar_range = bar['high'] - bar['low']
            atr_ok = bar_range >= atr7 * 2.1

            # BUY breakout
            if not had_buy and bar['high'] > opr_high and atr_ok:
                had_buy = True
                ep = opr_high
                tp_p = ep + 140
                sl_p = buy_sl
                position = {'dir': 'long', 'entry': ep, 'tp': tp_p, 'sl': sl_p,
                             'entry_time': str(bar['cet'])}

                exit_p = None; reason = None
                if bar['low'] <= sl_p and bar['high'] >= tp_p:
                    exit_p = sl_p; reason = 'sl'
                elif bar['low'] <= sl_p:
                    exit_p = sl_p; reason = 'sl'
                elif bar['high'] >= tp_p:
                    exit_p = tp_p; reason = 'tp'

                if exit_p is not None:
                    pnl_pts = exit_p - ep
                    pnl_eur = pnl_pts * point_value
                    trades.append({
                        'date': str(day), 'direction': 'long',
                        'entry': round(ep, 2), 'exit': round(exit_p, 2),
                        'entry_time': str(bar['cet']),
                        'exit_time': str(bar['cet']),
                        'exit_reason': reason,
                        'pnl_pts': round(pnl_pts, 2),
                        'pnl_usd': round(pnl_eur, 2),
                    })
                    equity.append(equity[-1] + pnl_eur)
                    day_pnl += pnl_eur
                    position = None
                entered = True

            # SELL breakout
            if not entered and not had_sell and bar['low'] < opr_low and atr_ok:
                had_sell = True
                ep = opr_low
                tp_p = ep - 185
                sl_p = sell_sl
                position = {'dir': 'short', 'entry': ep, 'tp': tp_p, 'sl': sl_p,
                             'entry_time': str(bar['cet'])}

                exit_p = None; reason = None
                if bar['high'] >= sl_p and bar['low'] <= tp_p:
                    exit_p = sl_p; reason = 'sl'
                elif bar['high'] >= sl_p:
                    exit_p = sl_p; reason = 'sl'
                elif bar['low'] <= tp_p:
                    exit_p = tp_p; reason = 'tp'

                if exit_p is not None:
                    pnl_pts = ep - exit_p
                    pnl_eur = pnl_pts * point_value
                    trades.append({
                        'date': str(day), 'direction': 'short',
                        'entry': round(ep, 2), 'exit': round(exit_p, 2),
                        'entry_time': str(bar['cet']),
                        'exit_time': str(bar['cet']),
                        'exit_reason': reason,
                        'pnl_pts': round(pnl_pts, 2),
                        'pnl_usd': round(pnl_eur, 2),
                    })
                    equity.append(equity[-1] + pnl_eur)
                    day_pnl += pnl_eur
                    position = None

    return trades, equity


def print_full_report(title, trades, equity, point_value):
    if not trades:
        print('{}: AUCUN TRADE'.format(title))
        return

    df = pd.DataFrame(trades)
    pnls = df['pnl_usd'].tolist()
    pnls_pts = df['pnl_pts'].tolist()
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    wr = len(wins) / len(pnls) * 100
    gross_win = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    avg_trade = np.mean(pnls)
    avg_trade_pts = np.mean(pnls_pts)
    ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 99

    wins_pts = [p for p in pnls_pts if p > 0]
    losses_pts = [p for p in pnls_pts if p < 0]
    avg_win_pts = np.mean(wins_pts) if wins_pts else 0
    avg_loss_pts = np.mean(losses_pts) if losses_pts else 0

    eq = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = abs(dd.min())

    print('=' * 85)
    print('  {}'.format(title))
    print('=' * 85)
    print('  Trades           : {}'.format(len(trades)))
    print('  Trades gagnants  : {} ({:.1f}%)'.format(len(wins), wr))
    print('  Trades perdants  : {}'.format(len(losses)))
    print('  PnL total        : EUR {:>+,.0f}'.format(total_pnl))
    print('  Profit Factor    : {}'.format(pf))
    print('  Gain moyen/Perte : {:.2f}'.format(ratio))
    print('  Trade moyen      : EUR {:>+,.0f} ({:+.1f} pts)'.format(avg_trade, avg_trade_pts))
    print('  Trade gagnant moy: EUR {:>+,.0f} ({:+.1f} pts)'.format(avg_win, avg_win_pts))
    print('  Trade perdant moy: EUR {:>+,.0f} ({:.1f} pts)'.format(avg_loss, avg_loss_pts))
    print('  Gain brut        : EUR {:>,.0f}'.format(gross_win))
    print('  Perte brute      : EUR {:>,.0f}'.format(gross_loss))
    print('  Max DD           : EUR {:>,.0f}'.format(max_dd))
    print('  Point value      : {} EUR/pt'.format(point_value))

    # Exit reasons
    reasons = df['exit_reason'].value_counts()
    print('  Sorties          : {}'.format(', '.join('{} {}x'.format(k, v) for k, v in reasons.items())))

    # Monthly
    df['date_parsed'] = pd.to_datetime(df['date'])
    df['month'] = df['date_parsed'].dt.to_period('M')

    print('\n  --- P&L Mensuel ---')
    total_cumul = 0
    for m, group in df.groupby('month'):
        m_pnl = group['pnl_usd'].sum()
        m_trades = len(group)
        m_wins = len(group[group['pnl_usd'] > 0])
        m_wr = m_wins / m_trades * 100 if m_trades else 0
        total_cumul += m_pnl
        marker = '+' if m_pnl > 0 else '-'
        print('  {}  |  {:>3}t ({:>2}W/{:>2}L)  |  WR {:5.1f}%  |  EUR {:>+10,.0f}  |  cumul {:>+10,.0f}  {}'.format(
            m, m_trades, m_wins, m_trades - m_wins, m_wr, m_pnl, total_cumul, marker))

    # Days summary
    daily = df.groupby('date').agg(
        trades=('pnl_usd', 'count'),
        pnl=('pnl_usd', 'sum'),
    ).reset_index()
    days_pos = (daily['pnl'] > 0).sum()
    days_neg = (daily['pnl'] < 0).sum()
    avg_td = daily['trades'].mean()
    print('\n  Jours: {}+ / {}- sur {} ({:.1f} trades/jour)'.format(
        days_pos, days_neg, len(daily), avg_td))


# ============================================================
# MAIN
# ============================================================
print('Loading FDXM 5min 12 mois (Mar 2025 - Mar 2026)...')
df_5m = pd.read_csv('data/databento_fdxm_5min_12mo.csv', index_col=0, parse_dates=True)
if df_5m.index.tz is None:
    df_5m.index = df_5m.index.tz_localize('UTC')
print('  {} bars | {} -> {}'.format(len(df_5m), df_5m.index.min().date(), df_5m.index.max().date()))

# Aussi charger seulement Mar-Jul 2025 pour comparer sur leur periode
df_overlap = df_5m[df_5m.index <= '2025-07-30']
print('  Overlap avec leur periode (Mar-Jul 2025): {} bars | {} -> {}'.format(
    len(df_overlap), df_overlap.index.min().date(), df_overlap.index.max().date()))

PV = POINT_VALUE_FDAX  # 25 EUR/pt

# ============================================================
# 1) OPR DAX — Periode overlap (Mar-Jul 2025) — 25 EUR/pt
# ============================================================
print('\n' + '#' * 85)
print('#  PERIODE OVERLAP: Mar 2025 - Jul 2025 (4.5 mois, fin de leur backtest)')
print('#  Comparaison identique: 1 FDAX = 25 EUR/pt')
print('#' * 85)

opr_overlap, eq_opr_overlap = run_opr_dax(df_overlap, point_value=PV)
print_full_report(
    'OPR DAX (Mar-Jul 2025) | 1 FDAX 25 EUR/pt | FLAT 13h',
    opr_overlap, eq_opr_overlap, PV
)

# ============================================================
# 2) MM20 Pullback — Periode overlap — 25 EUR/pt (cash-only)
# ============================================================
df_cash_overlap = df_overlap.copy()
cet_idx = df_cash_overlap.index.tz_convert(CET)
mask = (cet_idx.hour >= 8) & ((cet_idx.hour < 17) | ((cet_idx.hour == 17) & (cet_idx.minute <= 25)))
df_cash_overlap = df_cash_overlap[mask]

engine_overlap = MM20BacktestEngine(
    tp_points=300, trail_bars=20, max_sl_pts=200, max_trades_day=4,
    sma_period=20, start_offset_min=0,
    abs_start_hour=10, abs_start_min=0,
    daily_loss_stop=3, point_value=PV, daily_loss_usd=1500,
    pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
)
report_overlap = engine_overlap.run(df_cash_overlap)

if report_overlap and report_overlap.total_trades > 0:
    mm20_trades_o = report_overlap.trades
    mm20_eq_o = report_overlap.equity_curve
    print_full_report(
        'MM20 PULLBACK DAX (Mar-Jul 2025) | 1 FDAX 25 EUR/pt | cash-only 10h-close',
        mm20_trades_o, mm20_eq_o, PV
    )
else:
    print('  MM20 Pullback overlap: AUCUN TRADE')
    mm20_trades_o = []

# ============================================================
# 3) OPR DAX — 12 mois complets — 25 EUR/pt
# ============================================================
print('\n' + '#' * 85)
print('#  PERIODE COMPLETE: Mar 2025 - Mar 2026 (12 mois)')
print('#  1 FDAX = 25 EUR/pt')
print('#' * 85)

opr_full, eq_opr_full = run_opr_dax(df_5m, point_value=PV)
print_full_report(
    'OPR DAX (12 mois) | 1 FDAX 25 EUR/pt | FLAT 13h',
    opr_full, eq_opr_full, PV
)

# MM20 Pullback — 12 mois — 25 EUR/pt
df_cash = df_5m.copy()
cet_idx = df_cash.index.tz_convert(CET)
mask = (cet_idx.hour >= 8) & ((cet_idx.hour < 17) | ((cet_idx.hour == 17) & (cet_idx.minute <= 25)))
df_cash = df_cash[mask]

engine_full = MM20BacktestEngine(
    tp_points=300, trail_bars=20, max_sl_pts=200, max_trades_day=4,
    sma_period=20, start_offset_min=0,
    abs_start_hour=10, abs_start_min=0,
    daily_loss_stop=3, point_value=PV, daily_loss_usd=1500,
    pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
)
report_full = engine_full.run(df_cash)

if report_full and report_full.total_trades > 0:
    mm20_trades_f = report_full.trades
    mm20_eq_f = report_full.equity_curve
    print_full_report(
        'MM20 PULLBACK DAX (12 mois) | 1 FDAX 25 EUR/pt | cash-only 10h-close',
        mm20_trades_f, mm20_eq_f, PV
    )
else:
    print('  MM20 Pullback 12 mois: AUCUN TRADE')
    mm20_trades_f = []

# ============================================================
# 4) COMPARATIF FINAL
# ============================================================
print('\n')
print('=' * 90)
print('  COMPARATIF FINAL — 1 FDAX (25 EUR/pt)')
print('=' * 90)

def get_stats(trades, equity):
    if not trades:
        return {'n': 0, 'wr': 0, 'pnl': 0, 'pf': 0}
    pnls = [t['pnl_usd'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 0
    eq = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd = abs((eq - peak).min())
    return {
        'n': len(trades),
        'wr': len(wins) / len(pnls) * 100,
        'pnl': sum(pnls),
        'pf': round(gw / gl, 2) if gl > 0 else 99,
        'dd': dd,
    }

s_opr_o = get_stats(opr_overlap, eq_opr_overlap)
s_mm20_o = get_stats(mm20_trades_o, mm20_eq_o if mm20_trades_o else [0])
s_opr_f = get_stats(opr_full, eq_opr_full)
s_mm20_f = get_stats(mm20_trades_f, mm20_eq_f if mm20_trades_f else [0])

print('\n  OVERLAP (Mar-Jul 2025 = 4.5 mois)')
print('  {:25} {:>18} {:>18}'.format('', 'OPR 8h-9h', 'MM20 Pullback'))
print('  ' + '-' * 62)
print('  {:25} {:>18} {:>18}'.format('Trades', str(s_opr_o['n']), str(s_mm20_o['n'])))
print('  {:25} {:>17.1f}% {:>17.1f}%'.format('Win Rate', s_opr_o['wr'], s_mm20_o['wr']))
print('  {:25} {:>+17,.0f}E {:>+17,.0f}E'.format('PnL Total', s_opr_o['pnl'], s_mm20_o['pnl']))
print('  {:25} {:>18} {:>18}'.format('Profit Factor', str(s_opr_o['pf']), str(s_mm20_o['pf'])))
print('  {:25} {:>17,.0f}E {:>17,.0f}E'.format('Max Drawdown', s_opr_o.get('dd', 0), s_mm20_o.get('dd', 0)))

print('\n  COMPLET (12 mois)')
print('  {:25} {:>18} {:>18}'.format('', 'OPR 8h-9h', 'MM20 Pullback'))
print('  ' + '-' * 62)
print('  {:25} {:>18} {:>18}'.format('Trades', str(s_opr_f['n']), str(s_mm20_f['n'])))
print('  {:25} {:>17.1f}% {:>17.1f}%'.format('Win Rate', s_opr_f['wr'], s_mm20_f['wr']))
print('  {:25} {:>+17,.0f}E {:>+17,.0f}E'.format('PnL Total', s_opr_f['pnl'], s_mm20_f['pnl']))
print('  {:25} {:>18} {:>18}'.format('Profit Factor', str(s_opr_f['pf']), str(s_mm20_f['pf'])))
print('  {:25} {:>17,.0f}E {:>17,.0f}E'.format('Max Drawdown', s_opr_f.get('dd', 0), s_mm20_f.get('dd', 0)))

print('\n  Reference PDF: 224,200 EUR | 771t | WR 45.4% | PF 1.52 (Jan 2024 - Jul 2025)')
print('  Note: Jan 2024 - Feb 2025 non dispo sur Databento (XEUR.EOBI depuis mars 2025)')
