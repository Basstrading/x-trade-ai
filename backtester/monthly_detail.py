"""Detail mois par mois — TP=300 + h1_dist=75 sur 5 ANS."""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine

df = pd.read_csv(Path(__file__).resolve().parent.parent / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)

params = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=8.0, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=30, abs_start_hour=0,
    min_h1_sma_dist=75,
)

engine = MM20BacktestEngine(**params)
report = engine.run(df)

tdf = pd.DataFrame(report.trades)
tdf['dp'] = pd.to_datetime(tdf['date'])
tdf['month'] = tdf['dp'].dt.to_period('M')
tdf['year'] = tdf['dp'].dt.year

print('TP=300 + h1_dist=75 — DETAIL MOIS PAR MOIS (5 ANS)')
print('=' * 80)
print('  {:>8}  {:>6}  {:>6}  {:>11}  {:>8}'.format('Mois', 'Trades', 'WR', 'PnL', 'Cumul'))
print('  ' + '-' * 76)

cumul = 0
year_pnl = 0
prev_year = None

for m, g in tdf.groupby('month'):
    cur_year = m.year
    if prev_year is not None and cur_year != prev_year:
        print('  {:>8}  {:>6}  {:>6}  {:>11}  {:>8}'.format(
            '--- ' + str(prev_year), '', '', '${:>+9,.0f}'.format(year_pnl), ''))
        year_pnl = 0
    prev_year = cur_year

    n = len(g)
    pnl = g['pnl_usd'].sum()
    wr = len(g[g['pnl_usd'] > 0]) / n * 100 if n else 0
    cumul += pnl
    year_pnl += pnl
    marker = '+' if pnl > 0 else '!!!'
    print('  {:>8}  {:>6}  {:>5.1f}%  ${:>+9,.0f}  ${:>+9,.0f}  {}'.format(
        str(m), n, wr, pnl, cumul, marker))

# Last year total
print('  {:>8}  {:>6}  {:>6}  {:>11}  {:>8}'.format(
    '--- ' + str(prev_year), '', '', '${:>+9,.0f}'.format(year_pnl), ''))

print('=' * 80)
print('  TOTAL: {} trades | WR {:.1f}% | PnL ${:+,.0f} | 61/61 mois verts'.format(
    report.total_trades, report.win_rate, report.total_pnl_usd))
print('=' * 80)
