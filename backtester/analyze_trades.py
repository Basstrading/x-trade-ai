"""Analyse detaillee des trades MM20 Pullback pour trouver des axes d'amelioration."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

df = pd.read_csv(Path(__file__).resolve().parent.parent / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)

BASE = dict(
    tp_points=300, trail_bars=15, max_trades_day=4, sma_period=20,
    start_offset_min=30, min_sma_dist=0, atr_min=0, daily_loss_stop=2,
    point_value=8.0, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
)
engine = MM20BacktestEngine(**BASE)
report = engine.run(df)

t = pd.DataFrame(report.trades)
t['dp'] = pd.to_datetime(t['entry_time'], utc=True)
t['hour'] = t['dp'].dt.hour
t['dow'] = t['dp'].dt.dayofweek
t['dow_name'] = t['dp'].dt.day_name()

print('=== PnL PAR HEURE D ENTREE (Paris) ===')
for h, g in t.groupby('hour'):
    n = len(g)
    pnl = g['pnl_usd'].sum()
    wr = len(g[g['pnl_usd'] > 0]) / n * 100
    avg = g['pnl_usd'].mean()
    wins = g[g['pnl_usd'] > 0]['pnl_usd'].sum()
    losses = abs(g[g['pnl_usd'] < 0]['pnl_usd'].sum())
    pf = wins / losses if losses > 0 else 99
    print(f'  {h:02d}h | {n:>4} trades | WR {wr:5.1f}% | PnL ${pnl:>+9,.0f} | Avg ${avg:>+6,.0f} | PF {pf:.2f}')

print()
print('=== PnL PAR JOUR DE LA SEMAINE ===')
for d, g in t.groupby('dow'):
    n = len(g)
    pnl = g['pnl_usd'].sum()
    wr = len(g[g['pnl_usd'] > 0]) / n * 100
    avg = g['pnl_usd'].mean()
    wins = g[g['pnl_usd'] > 0]['pnl_usd'].sum()
    losses = abs(g[g['pnl_usd'] < 0]['pnl_usd'].sum())
    pf = wins / losses if losses > 0 else 99
    name = g['dow_name'].iloc[0]
    print(f'  {name:<10} | {n:>4} trades | WR {wr:5.1f}% | PnL ${pnl:>+9,.0f} | Avg ${avg:>+6,.0f} | PF {pf:.2f}')

print()
print('=== PnL PAR DIRECTION ===')
for d, g in t.groupby('direction'):
    n = len(g)
    pnl = g['pnl_usd'].sum()
    wr = len(g[g['pnl_usd'] > 0]) / n * 100
    avg = g['pnl_usd'].mean()
    wins = g[g['pnl_usd'] > 0]['pnl_usd'].sum()
    losses = abs(g[g['pnl_usd'] < 0]['pnl_usd'].sum())
    pf = wins / losses if losses > 0 else 99
    print(f'  {d:<6} | {n:>4} trades | WR {wr:5.1f}% | PnL ${pnl:>+9,.0f} | Avg ${avg:>+6,.0f} | PF {pf:.2f}')

print()
print('=== PnL PAR RAISON DE SORTIE ===')
for r2, g in t.groupby('exit_reason'):
    n = len(g)
    pnl = g['pnl_usd'].sum()
    avg = g['pnl_usd'].mean()
    wr = len(g[g['pnl_usd'] > 0]) / n * 100
    print(f'  {r2:<12} | {n:>4} trades | WR {wr:5.1f}% | PnL ${pnl:>+9,.0f} | Avg ${avg:>+6,.0f}')

print()
print('=== DISTRIBUTION DES PnL (pts) ===')
pnl_pts = t['pnl_pts']
for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
    print(f'  P{p:>2}: {pnl_pts.quantile(p/100):>+8.1f} pts')

print()
print('=== PnL PAR RANG DU TRADE DANS LA JOURNEE ===')
t['date_str'] = t['date']
t['rank'] = t.groupby('date_str').cumcount() + 1
for r2, g in t.groupby('rank'):
    n = len(g)
    pnl = g['pnl_usd'].sum()
    wr = len(g[g['pnl_usd'] > 0]) / n * 100
    avg = g['pnl_usd'].mean()
    wins = g[g['pnl_usd'] > 0]['pnl_usd'].sum()
    losses = abs(g[g['pnl_usd'] < 0]['pnl_usd'].sum())
    pf = wins / losses if losses > 0 else 99
    print(f'  Trade #{r2} | {n:>4} trades | WR {wr:5.1f}% | PnL ${pnl:>+9,.0f} | Avg ${avg:>+6,.0f} | PF {pf:.2f}')

# Gros perdants
big_losers = t[t['pnl_usd'] < -500]
print()
print(f'=== GROS PERDANTS (< -$500) : {len(big_losers)} trades ===')
print(f'  PnL total: ${big_losers["pnl_usd"].sum():>+,.0f}')
print(f'  Par heure:')
for h, g in big_losers.groupby('hour'):
    print(f'    {h:02d}h: {len(g)} trades, ${g["pnl_usd"].sum():>+,.0f}')
print(f'  Par direction:')
for d, g in big_losers.groupby('direction'):
    print(f'    {d}: {len(g)} trades, ${g["pnl_usd"].sum():>+,.0f}')

# Duree moyenne des trades
t['exit_dp'] = pd.to_datetime(t['exit_time'], utc=True)
t['duration_min'] = (t['exit_dp'] - t['dp']).dt.total_seconds() / 60
print()
print('=== DUREE DES TRADES ===')
winners = t[t['pnl_usd'] > 0]
losers = t[t['pnl_usd'] < 0]
print(f'  Gagnants : duree moyenne {winners["duration_min"].mean():.0f} min, mediane {winners["duration_min"].median():.0f} min')
print(f'  Perdants : duree moyenne {losers["duration_min"].mean():.0f} min, mediane {losers["duration_min"].median():.0f} min')

# Trades qui ont ete en gain avant d'etre stoppes en perte
print()
print('=== TRAIL STOP: PnL des sorties trail_stop ===')
ts_trades = t[t['exit_reason'] == 'trail_stop']
ts_win = ts_trades[ts_trades['pnl_usd'] > 0]
ts_loss = ts_trades[ts_trades['pnl_usd'] < 0]
print(f'  Gagnants: {len(ts_win)} trades, PnL ${ts_win["pnl_usd"].sum():>+,.0f}, Avg ${ts_win["pnl_usd"].mean():>+,.0f}')
print(f'  Perdants: {len(ts_loss)} trades, PnL ${ts_loss["pnl_usd"].sum():>+,.0f}, Avg ${ts_loss["pnl_usd"].mean():>+,.0f}')
print(f'  Distribution PnL trail_stop (pts):')
for p in [10, 25, 50, 75, 90]:
    print(f'    P{p}: {ts_trades["pnl_pts"].quantile(p/100):>+8.1f} pts')
