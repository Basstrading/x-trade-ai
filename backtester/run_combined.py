"""Combined: MM20 Pullback + News Trading sur 5 ans."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtester.mm20_engine import MM20BacktestEngine
from backtester.news_engine import NewsBacktestEngine

BASE_DIR = Path(__file__).resolve().parent.parent
df = pd.read_csv(BASE_DIR / 'data' / 'databento_nq_5min_5y.csv',
                 index_col=0, parse_dates=True)
PV = 8.0

print("Data: {} bars, 5 ans".format(len(df)))
print()

# === 1. MM20 Pullback (best config) ===
mm20_params = dict(
    tp_points=300, trail_bars=20, max_trades_day=4, sma_period=20,
    min_sma_dist=0, max_sma_dist=0, atr_min=0, daily_loss_stop=3,
    point_value=PV, daily_loss_usd=1000, pullback_bars=10, pullback_dist=15,
    max_sl_pts=200, start_offset_min=30, abs_start_hour=0,
    min_h1_sma_dist=75,
)

mm20_engine = MM20BacktestEngine(**mm20_params)
mm20_report = mm20_engine.run(df)

# === 2. News Trading (best config) ===
news_engine = NewsBacktestEngine(
    lookback_min=30, min_move_pts=10, entry_before_min=2,
    wide_sl_pts=150, tp_pts=200, max_hold_min=60,
    point_value=PV, tier_filter=1,
)
news_report = news_engine.run(df)

# === 3. Combine trades ===
mm20_trades = pd.DataFrame(mm20_report.trades)
mm20_trades['strategy'] = 'MM20'
mm20_trades['sort_date'] = pd.to_datetime(mm20_trades['date'])

news_trades = pd.DataFrame(news_report.trades)
news_trades['strategy'] = 'NEWS'
news_trades['sort_date'] = pd.to_datetime(news_trades['date'])

# Check overlap: news trades happen around 8:30 ET (14:30 Paris), MM20 starts 16h Paris
# They should NOT overlap, but let's verify
print("=== VERIFICATION CHEVAUCHEMENT ===")
mm20_dates = set(mm20_trades['date'].values)
news_dates_set = set(news_trades['date'].values)
overlap = mm20_dates & news_dates_set
print("Jours avec les 2 strategies: {}/{}".format(len(overlap), len(news_dates_set)))
print()

# Combine all trades chronologically
all_trades = pd.concat([mm20_trades, news_trades], ignore_index=True)
all_trades = all_trades.sort_values('sort_date').reset_index(drop=True)

# === STATS INDIVIDUELLES ===
print('=' * 100)
print('  RESULTATS INDIVIDUELS')
print('=' * 100)

for name, report in [('MM20 Pullback h1d=75', mm20_report), ('News Tier1', news_report)]:
    tdf = pd.DataFrame(report.trades)
    tdf['dp'] = pd.to_datetime(tdf['date'])
    tdf['month'] = tdf['dp'].dt.to_period('M')
    mp = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() > 0)
    mn = sum(1 for _, g in tdf.groupby('month') if g['pnl_usd'].sum() <= 0)
    print("  {:<25} {:>5} tr | WR {:>5.1f}% | ${:>+10,.0f} | PF {:>5.2f} | Sh {:>5.2f} | DD ${:>7,.0f} | {}/{}M".format(
        name, report.total_trades, report.win_rate, report.total_pnl_usd,
        report.profit_factor, report.sharpe_ratio, report.max_drawdown_usd, mp, mp+mn))

# === STATS COMBINEES ===
print()
print('=' * 100)
print('  RESULTATS COMBINES (MM20 + NEWS)')
print('=' * 100)

pnls = all_trades['pnl_usd'].values
wins = pnls[pnls > 0]
losses = pnls[pnls <= 0]
total_pnl = pnls.sum()
wr = len(wins) / len(pnls) * 100
pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else 99
avg_win = wins.mean() if len(wins) > 0 else 0
avg_loss = losses.mean() if len(losses) > 0 else 0
sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252) if np.std(pnls) > 0 else 0

cum = np.cumsum(pnls)
peak = np.maximum.accumulate(cum)
dd = peak - cum
max_dd = dd.max()

print("  Trades:  {}".format(len(pnls)))
print("  WR:      {:.1f}%".format(wr))
print("  PnL:     ${:+,.0f}".format(total_pnl))
print("  PF:      {:.2f}".format(pf))
print("  Sharpe:  {:.2f}".format(sharpe))
print("  MaxDD:   ${:,.0f}".format(max_dd))
print("  AvgWin:  ${:+,.0f}".format(avg_win))
print("  AvgLoss: ${:+,.0f}".format(avg_loss))

# Monthly breakdown combined
all_trades['month'] = all_trades['sort_date'].dt.to_period('M')
mp_c = sum(1 for _, g in all_trades.groupby('month') if g['pnl_usd'].sum() > 0)
mn_c = sum(1 for _, g in all_trades.groupby('month') if g['pnl_usd'].sum() <= 0)
print("  Mois+:   {}/{}".format(mp_c, mp_c + mn_c))

# === DETAIL MOIS PAR MOIS ===
print()
print('=' * 100)
print('  DETAIL MOIS PAR MOIS (COMBINE)')
print('=' * 100)
print('  {:>8}  {:>5} {:>5}  {:>6}  {:>11}  {:>11}'.format(
    'Mois', 'MM20', 'NEWS', 'WR', 'PnL', 'Cumul'))
print('  ' + '-' * 80)

cumul = 0
year_pnl = 0
prev_year = None

for m, g in all_trades.groupby('month'):
    cur_year = m.year
    if prev_year is not None and cur_year != prev_year:
        print('  {:>8}  {:>5} {:>5}  {:>6}  {:>11}'.format(
            '--- ' + str(prev_year), '', '', '', '${:>+9,.0f}'.format(year_pnl)))
        year_pnl = 0
    prev_year = cur_year

    n = len(g)
    mm20_n = len(g[g['strategy'] == 'MM20'])
    news_n = len(g[g['strategy'] == 'NEWS'])
    pnl = g['pnl_usd'].sum()
    wr_m = len(g[g['pnl_usd'] > 0]) / n * 100 if n else 0
    cumul += pnl
    year_pnl += pnl
    marker = '+' if pnl > 0 else '!!!'
    print('  {:>8}  {:>5} {:>5}  {:>5.1f}%  ${:>+9,.0f}  ${:>+9,.0f}  {}'.format(
        str(m), mm20_n, news_n, wr_m, pnl, cumul, marker))

# Last year total
print('  {:>8}  {:>5} {:>5}  {:>6}  {:>11}'.format(
    '--- ' + str(prev_year), '', '', '', '${:>+9,.0f}'.format(year_pnl)))

# === YEARLY SUMMARY ===
print()
print('=' * 100)
print('  RESUME PAR ANNEE')
print('=' * 100)
all_trades['year'] = all_trades['sort_date'].dt.year

print('  {:>6}  {:>6} {:>6} {:>6}  {:>6}  {:>11}  {:>11}  {:>11}'.format(
    'Annee', 'Total', 'MM20', 'NEWS', 'WR', 'PnL MM20', 'PnL NEWS', 'PnL TOTAL'))
print('  ' + '-' * 80)

for y, g in all_trades.groupby('year'):
    mm20_g = g[g['strategy'] == 'MM20']
    news_g = g[g['strategy'] == 'NEWS']
    wr_y = len(g[g['pnl_usd'] > 0]) / len(g) * 100 if len(g) > 0 else 0
    print('  {:>6}  {:>6} {:>6} {:>6}  {:>5.1f}%  ${:>+9,.0f}  ${:>+9,.0f}  ${:>+9,.0f}'.format(
        int(y), len(g), len(mm20_g), len(news_g), wr_y,
        mm20_g['pnl_usd'].sum(), news_g['pnl_usd'].sum(),
        g['pnl_usd'].sum()))

print()
print('=' * 100)
