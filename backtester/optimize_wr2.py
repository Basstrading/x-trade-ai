"""
ROUND 2 — Combinaisons autour de atr_min=25 + max_trades=5
============================================================
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
PV = 8.0

df_full = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                      index_col=0, parse_dates=True)
df = df_full[df_full.index >= '2025-09-10'].copy()
print("6 mois: {} bars".format(len(df)))

BASE = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=0, abs_start_hour=8, abs_start_min=0,
)

def run(params, data=df):
    engine = MM20BacktestEngine(**params)
    report = engine.run(data)
    if not report or report.total_trades < 10:
        return None
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    return dict(trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
                pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
                avg_trade=report.avg_trade, mp=mp, mn=mn)

def show(name, r):
    if not r:
        print("  {:<55} NO TRADES".format(name))
        return
    tm = r['mp'] + r['mn']
    print("  {:<55} {:>4} tr | WR {:>5.1f}% | PnL ${:>+9,.0f} | PF {:>5.2f} | Sh {:>5.2f} | DD ${:>7,.0f} | {}/{}M".format(
        name, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'], r['mp'], tm))

combos = [
    ('BASE 8h00',                                    BASE),
    ('atr=25',                                       dict(BASE, atr_min=25)),
    ('atr=22',                                       dict(BASE, atr_min=22)),
    ('atr=20',                                       dict(BASE, atr_min=20)),
    ('atr=25 + max_tr=5',                            dict(BASE, atr_min=25, max_trades_day=5)),
    ('atr=25 + max_tr=6',                            dict(BASE, atr_min=25, max_trades_day=6)),
    ('atr=22 + max_tr=5',                            dict(BASE, atr_min=22, max_trades_day=5)),
    ('atr=20 + max_tr=5',                            dict(BASE, atr_min=20, max_trades_day=5)),
    ('atr=25 + tp=400',                              dict(BASE, atr_min=25, tp_points=400)),
    ('atr=25 + tp=250',                              dict(BASE, atr_min=25, tp_points=250)),
    ('atr=25 + tp=200',                              dict(BASE, atr_min=25, tp_points=200)),
    ('atr=25 + max_tr=5 + tp=400',                   dict(BASE, atr_min=25, max_trades_day=5, tp_points=400)),
    ('atr=25 + max_tr=5 + tp=250',                   dict(BASE, atr_min=25, max_trades_day=5, tp_points=250)),
    ('atr=25 + max_tr=5 + trail=25',                 dict(BASE, atr_min=25, max_trades_day=5, trail_bars=25)),
    ('atr=25 + max_tr=5 + min_sma=15',               dict(BASE, atr_min=25, max_trades_day=5, min_sma_dist=15)),
    ('atr=25 + max_tr=5 + min_sma=20',               dict(BASE, atr_min=25, max_trades_day=5, min_sma_dist=20)),
    ('atr=25 + max_tr=5 + dlu=750',                  dict(BASE, atr_min=25, max_trades_day=5, daily_loss_usd=750)),
    ('atr=25 + max_tr=6 + tp=400',                   dict(BASE, atr_min=25, max_trades_day=6, tp_points=400)),
    ('atr=22 + max_tr=5 + tp=400',                   dict(BASE, atr_min=22, max_trades_day=5, tp_points=400)),
    ('atr=20 + max_tr=5 + tp=400',                   dict(BASE, atr_min=20, max_trades_day=5, tp_points=400)),
    ('atr=25 + max_tr=5 + tp=400 + min_sma=15',      dict(BASE, atr_min=25, max_trades_day=5, tp_points=400, min_sma_dist=15)),
    ('atr=25 + max_tr=5 + tp=400 + trail=25',        dict(BASE, atr_min=25, max_trades_day=5, tp_points=400, trail_bars=25)),
    ('atr=25 + max_tr=5 + tp=400 + max_sl=250',      dict(BASE, atr_min=25, max_trades_day=5, tp_points=400, max_sl_pts=250)),
]

print()
print('=' * 130)
print('  ROUND 2: COMBINAISONS ATR + MAX TRADES + TP — 6 MOIS')
print('=' * 130)

for name, params in combos:
    show(name, run(params))

# === VALIDATION 5 ANS pour les meilleures ===
print()
print('=' * 130)
print('  VALIDATION SUR 5 ANS')
print('=' * 130)

best_5y = [
    ('BASE 8h00 (5y)',                               BASE),
    ('atr=25 + max_tr=5 (5y)',                       dict(BASE, atr_min=25, max_trades_day=5)),
    ('atr=25 + max_tr=5 + tp=400 (5y)',              dict(BASE, atr_min=25, max_trades_day=5, tp_points=400)),
    ('atr=25 + max_tr=6 + tp=400 (5y)',              dict(BASE, atr_min=25, max_trades_day=6, tp_points=400)),
    ('atr=22 + max_tr=5 + tp=400 (5y)',              dict(BASE, atr_min=22, max_trades_day=5, tp_points=400)),
    ('atr=20 + max_tr=5 + tp=400 (5y)',              dict(BASE, atr_min=20, max_trades_day=5, tp_points=400)),
    ('atr=25 + max_tr=5 + tp=400 + trail=25 (5y)',   dict(BASE, atr_min=25, max_trades_day=5, tp_points=400, trail_bars=25)),
    ('16h ref best (5y)',                             dict(BASE, abs_start_hour=0, start_offset_min=30, atr_min=0, max_trades_day=4)),
]

for name, params in best_5y:
    show(name, run(params, data=df_full))

print('=' * 130)
