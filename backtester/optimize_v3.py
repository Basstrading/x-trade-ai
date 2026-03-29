"""
OPTIMISATION V3 — Breakeven, candle filter, H1 SMA distance
=============================================================
Base: 16h + max_sl=200 + trail20 + dls=3 (meilleure 5 ans)
Validation sur 5 ANS complets.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
PV = 8.0

df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
print("Data: {} bars, 5 ans\n".format(len(df)))

BEST = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=30, abs_start_hour=0,
)

def run(params):
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    if not report or report.total_trades < 50:
        return None
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    # exit reasons
    reasons = {}
    for reason, g in tdf.groupby('exit_reason'):
        reasons[reason] = {'n': len(g), 'pnl': g['pnl_usd'].sum(),
                           'wr': len(g[g['pnl_usd'] > 0]) / len(g) * 100 if len(g) > 0 else 0}
    return dict(trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
                pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                avg_trade=report.avg_trade, avg_win=report.avg_win, avg_loss=report.avg_loss,
                mp=mp, mn=mn, reasons=reasons)

def show(name, r):
    if not r:
        print("  {:<55} NO TRADES".format(name))
        return
    tm = r['mp'] + r['mn']
    print("  {:<55} {:>5} tr | WR {:>5.1f}% | ${:>+9,.0f} | PF {:>5.2f} | Sh {:>5.2f} | DD ${:>7,.0f} | {}/{}M".format(
        name, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'], r['mp'], tm))

# === REFERENCE ===
print('=' * 130)
print('  REFERENCE')
print('=' * 130)
ref = run(BEST)
show('REFERENCE (16h + sl200 + trail20 + dls3)', ref)

# === TEST 1: Breakeven ===
print()
print('--- Test 1: Breakeven (stop -> entry apres X pts de gain) ---')
for be in [10, 15, 20, 25, 30, 40, 50, 60, 75, 100]:
    r = run(dict(BEST, breakeven_pts=be))
    show('breakeven={}pts'.format(be), r)

# === TEST 2: Candle direction filter ===
print()
print('--- Test 2: Candle direction filter ---')
r = run(dict(BEST, candle_dir_filter=True))
show('candle_dir=True', r)

# === TEST 3: H1 SMA distance ===
print()
print('--- Test 3: Min H1 SMA distance ---')
for d in [5, 10, 15, 20, 25, 30, 40, 50, 75, 100]:
    r = run(dict(BEST, min_h1_sma_dist=d))
    show('h1_sma_dist={}'.format(d), r)

# === TEST 4: Combos ===
print()
print('=' * 130)
print('  COMBINAISONS')
print('=' * 130)

combos = [
    ('REFERENCE',                              BEST),
    ('be=20',                                  dict(BEST, breakeven_pts=20)),
    ('be=30',                                  dict(BEST, breakeven_pts=30)),
    ('be=40',                                  dict(BEST, breakeven_pts=40)),
    ('be=50',                                  dict(BEST, breakeven_pts=50)),
    ('candle_dir',                             dict(BEST, candle_dir_filter=True)),
    ('h1_dist=20',                             dict(BEST, min_h1_sma_dist=20)),
    ('h1_dist=30',                             dict(BEST, min_h1_sma_dist=30)),
    ('be=30 + candle_dir',                     dict(BEST, breakeven_pts=30, candle_dir_filter=True)),
    ('be=30 + h1_dist=20',                     dict(BEST, breakeven_pts=30, min_h1_sma_dist=20)),
    ('be=30 + h1_dist=30',                     dict(BEST, breakeven_pts=30, min_h1_sma_dist=30)),
    ('be=40 + candle_dir',                     dict(BEST, breakeven_pts=40, candle_dir_filter=True)),
    ('be=40 + h1_dist=20',                     dict(BEST, breakeven_pts=40, min_h1_sma_dist=20)),
    ('be=40 + h1_dist=30',                     dict(BEST, breakeven_pts=40, min_h1_sma_dist=30)),
    ('be=50 + candle_dir',                     dict(BEST, breakeven_pts=50, candle_dir_filter=True)),
    ('be=50 + h1_dist=20',                     dict(BEST, breakeven_pts=50, min_h1_sma_dist=20)),
    ('candle_dir + h1_dist=20',                dict(BEST, candle_dir_filter=True, min_h1_sma_dist=20)),
    ('candle_dir + h1_dist=30',                dict(BEST, candle_dir_filter=True, min_h1_sma_dist=30)),
    ('be=30 + candle_dir + h1_dist=20',        dict(BEST, breakeven_pts=30, candle_dir_filter=True, min_h1_sma_dist=20)),
    ('be=40 + candle_dir + h1_dist=20',        dict(BEST, breakeven_pts=40, candle_dir_filter=True, min_h1_sma_dist=20)),
    ('be=50 + candle_dir + h1_dist=20',        dict(BEST, breakeven_pts=50, candle_dir_filter=True, min_h1_sma_dist=20)),
    ('be=40 + candle_dir + h1_dist=30',        dict(BEST, breakeven_pts=40, candle_dir_filter=True, min_h1_sma_dist=30)),
]

results = []
for name, params in combos:
    r = run(params)
    if r:
        r['label'] = name
        results.append(r)

results.sort(key=lambda x: x['sharpe'], reverse=True)

print()
print("  {:<50} {:>5} {:>6} {:>11} {:>5} {:>6} {:>9} {:>5} {:>8} {:>8}".format(
    'Config', 'Tr', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'M+', 'AvgWin', 'AvgLoss'))
print('  ' + '-' * 120)
for r in results:
    tm = r['mp'] + r['mn']
    print("  {:<50} {:>5} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>7,.0f} {}/{} ${:>+6,.0f} ${:>+6,.0f}".format(
        r['label'], r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
        r['mp'], tm, r['avg_win'], r['avg_loss']))

# Show trail_stop breakdown for top 3
print()
print('--- Detail sorties trail_stop pour top configs ---')
for r in results[:5]:
    ts = r['reasons'].get('trail_stop', {'n': 0, 'pnl': 0, 'wr': 0})
    tp = r['reasons'].get('tp', {'n': 0, 'pnl': 0, 'wr': 0})
    tm_r = r['reasons'].get('time', {'n': 0, 'pnl': 0, 'wr': 0})
    print("  {:<40} trail: {} tr WR {:.0f}% ${:+,.0f} | tp: {} tr ${:+,.0f} | time: {} tr WR {:.0f}% ${:+,.0f}".format(
        r['label'], ts['n'], ts['wr'], ts['pnl'], tp['n'], tp['pnl'], tm_r['n'], tm_r['wr'], tm_r['pnl']))

print('=' * 130)
