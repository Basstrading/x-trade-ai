"""Verification des chiffres TP=240 vs TP=300 vs ancienne ref."""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

df = pd.read_csv(Path(__file__).resolve().parent.parent / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
PV = 8.0

base = dict(
    trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=30, abs_start_hour=0,
)

configs = [
    ('TP=300 SANS h1_dist (ancienne ref)', dict(base, tp_points=300, min_h1_sma_dist=0)),
    ('TP=300 + h1_dist=75',                dict(base, tp_points=300, min_h1_sma_dist=75)),
    ('TP=240 + h1_dist=75',                dict(base, tp_points=240, min_h1_sma_dist=75)),
]

for name, params in configs:
    engine = MM20BacktestEngine(**params)
    report = engine.run(df)

    print('=== {} ==='.format(name))
    print('  Trades: {}'.format(report.total_trades))
    print('  WR: {}%'.format(report.win_rate))
    print('  PnL: ${:+,.2f}'.format(report.total_pnl_usd))
    print('  PF: {}'.format(report.profit_factor))
    print('  Sharpe: {}'.format(report.sharpe_ratio))
    print('  MaxDD: ${:,.2f}'.format(report.max_drawdown_usd))

    tdf = pd.DataFrame(report.trades)
    print('  Sorties:')
    for reason, g in tdf.groupby('exit_reason'):
        n = len(g)
        pnl = g['pnl_usd'].sum()
        wr = len(g[g['pnl_usd'] > 0]) / n * 100
        print('    {}: {} trades, PnL ${:+,.0f}, WR {:.1f}%'.format(reason, n, pnl, wr))

    # Monthly
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    print('  Mois positifs: {}/{}'.format(mp, mp + mn))

    # 5 premiers et 5 derniers trades
    print('  5 premiers trades:')
    for _, t in tdf.head(5).iterrows():
        print('    {} {} entry={:.2f} exit={:.2f} pnl={:+.2f}pts ${:+,.0f} ({})'.format(
            t['date'], t['direction'], t['entry'], t['exit'], t['pnl_pts'], t['pnl_usd'], t['exit_reason']))
    print()
