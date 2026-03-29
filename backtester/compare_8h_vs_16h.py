"""
COMPARATIF : 16h00 Paris vs 8h00 Paris — 6 derniers mois
=========================================================
Config: max_sl=200 + trail20 + dls=3
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
PV = 8.0

# Charger 5 ans puis filtrer 6 derniers mois
df_full = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                      index_col=0, parse_dates=True)

# 6 derniers mois = depuis sept 2025
cutoff = '2025-09-10'
df = df_full[df_full.index >= cutoff].copy()
print("Data: {} bars, {} -> {}".format(len(df), df.index.min().date(), df.index.max().date()))

# Params communs (meilleure config)
BEST = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200,
)

configs = [
    ('16h00 Paris (ref)',   dict(BEST, start_offset_min=30, abs_start_hour=0)),
    ('8h00 Paris',          dict(BEST, start_offset_min=0, abs_start_hour=8, abs_start_min=0)),
    ('9h00 Paris',          dict(BEST, start_offset_min=0, abs_start_hour=9, abs_start_min=0)),
    ('10h00 Paris',         dict(BEST, start_offset_min=0, abs_start_hour=10, abs_start_min=0)),
    ('11h00 Paris',         dict(BEST, start_offset_min=0, abs_start_hour=11, abs_start_min=0)),
    ('12h00 Paris',         dict(BEST, start_offset_min=0, abs_start_hour=12, abs_start_min=0)),
    ('14h00 Paris',         dict(BEST, start_offset_min=0, abs_start_hour=14, abs_start_min=0)),
    ('15h30 Paris',         dict(BEST, start_offset_min=0, abs_start_hour=15, abs_start_min=30)),
]

def run_report(params):
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)
    if not report or report.total_trades < 5:
        return None
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)

    # PnL par heure
    tdf['entry_dt'] = pd.to_datetime(tdf['entry_time'], utc=True)
    tdf['entry_hour'] = tdf['entry_dt'].dt.hour
    hourly = {}
    for h, g in tdf.groupby('entry_hour'):
        hourly[h] = {'n': len(g), 'pnl': g['pnl_usd'].sum(),
                     'wr': len(g[g['pnl_usd'] > 0]) / len(g) * 100}

    # Direction
    dir_stats = {}
    for d, g in tdf.groupby('direction'):
        dir_stats[d] = {'n': len(g), 'pnl': g['pnl_usd'].sum(),
                        'wr': len(g[g['pnl_usd'] > 0]) / len(g) * 100}

    return dict(
        trades=report.total_trades, wr=report.win_rate, pnl=report.total_pnl_usd,
        pf=report.profit_factor, sharpe=report.sharpe_ratio, max_dd=report.max_drawdown_usd,
        avg_trade=report.avg_trade, avg_win=report.avg_win, avg_loss=report.avg_loss,
        mp=mp, mn=mn, hourly=hourly, dir_stats=dir_stats, report=report,
    )

print()
print('=' * 110)
print('  COMPARATIF HORAIRES DE TRADING — 6 DERNIERS MOIS')
print('=' * 110)
hdr = "  {:<20} {:>6} {:>6} {:>11} {:>5} {:>6} {:>9} {:>8} {:>8} {:>9} {:>5}".format(
    'Config', 'Trades', 'WR', 'PnL', 'PF', 'Sharp', 'MaxDD', 'AvgTr', 'AvgWin', 'AvgLoss', 'M+')
print(hdr)
print('  ' + '-' * 106)

all_results = {}
for name, params in configs:
    r = run_report(params)
    if r:
        all_results[name] = r
        tm = r['mp'] + r['mn']
        pct_str = "{}/{}".format(r['mp'], tm)
        line = "  {:<20} {:>6} {:>5.1f}% ${:>+9,.0f} {:>5.2f} {:>5.2f} ${:>8,.0f} ${:>+6,.0f} ${:>+6,.0f} ${:>+7,.0f} {:>5}".format(
            name, r['trades'], r['wr'], r['pnl'], r['pf'], r['sharpe'], r['max_dd'],
            r['avg_trade'], r['avg_win'], r['avg_loss'], pct_str)
        print(line)

# Detail par heure pour les 2 principales configs
for cfg_name in ['16h00 Paris (ref)', '8h00 Paris']:
    if cfg_name not in all_results:
        continue
    r = all_results[cfg_name]
    print()
    print('  --- {} : PnL par heure ---'.format(cfg_name))
    for h in sorted(r['hourly'].keys()):
        info = r['hourly'][h]
        print('    {:02d}h : {:>3} trades | WR {:>5.1f}% | PnL ${:>+8,.0f}'.format(
            h, info['n'], info['wr'], info['pnl']))

    print('  --- {} : Par direction ---'.format(cfg_name))
    for d in sorted(r['dir_stats'].keys()):
        info = r['dir_stats'][d]
        print('    {} : {:>3} trades | WR {:>5.1f}% | PnL ${:>+8,.0f}'.format(
            d, info['n'], info['wr'], info['pnl']))

# Mois par mois pour les 2 configs
for cfg_name in ['16h00 Paris (ref)', '8h00 Paris']:
    if cfg_name not in all_results:
        continue
    r = all_results[cfg_name]
    tdf = pd.DataFrame(r['report'].trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    print()
    print('  --- {} : PnL par mois ---'.format(cfg_name))
    for m, g in tdf.groupby('month'):
        m_pnl = g['pnl_usd'].sum()
        m_trades = len(g)
        m_wr = len(g[g['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
        marker = "+" if m_pnl > 0 else "!!!"
        print('    {}  |  {:>3} trades  |  WR {:>5.1f}%  |  PnL ${:>+9,.0f}  {}'.format(
            m, m_trades, m_wr, m_pnl, marker))

print()
print('=' * 110)
