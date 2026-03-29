"""
Backtest MM20 Pullback sur le DAX — 6 derniers mois
=====================================================
Donnees: Yahoo Finance (^GDAXI)
 - 5min: ~60 jours (3 mois) -> backtest precis
 - 1h: 6 mois -> backtest adapte (trail_bars ajuste)

Adaptation pour le DAX:
 - Session cash 9:00-17:30 CET -> start a 10h CET
 - Point value: 5 EUR/pt (1 Mini-DAX FDXM)
 - TP/SL/Pullback params identiques au NQ (proportionnels ~ok)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtester.mm20_engine import MM20BacktestEngine
from backtester.opr_engine import is_dst_gap
import pytz

PARIS = pytz.timezone('Europe/Paris')

# ============================================================
# 1) Telecharger les donnees DAX
# ============================================================
print('Telechargement DAX Yahoo Finance...')

# 5min: max ~60 jours
df_5m = yf.download('^GDAXI', period='60d', interval='5m', progress=False)
df_5m.columns = [c[0].lower() for c in df_5m.columns]
if df_5m.index.tz is None:
    df_5m.index = df_5m.index.tz_localize('UTC')
print('  5min: {} bars | {} -> {}'.format(len(df_5m), df_5m.index.min().date(), df_5m.index.max().date()))

# 1h: 6 mois
df_1h_full = yf.download('^GDAXI', period='6mo', interval='1h', progress=False)
df_1h_full.columns = [c[0].lower() for c in df_1h_full.columns]
if df_1h_full.index.tz is None:
    df_1h_full.index = df_1h_full.index.tz_localize('UTC')
print('  1h:   {} bars | {} -> {}'.format(len(df_1h_full), df_1h_full.index.min().date(), df_1h_full.index.max().date()))

# Daily pour le backtest 1h (filtre trend timeframe superieur)
df_daily = df_1h_full.resample('1D').agg({
    'open': 'first', 'high': 'max', 'low': 'min',
    'close': 'last', 'volume': 'sum'
}).dropna()

# ============================================================
# 2) Parametres MM20 Pullback adaptes au DAX
# ============================================================
# NQ: TP=300pts (~1.2%), SL=200pts (~0.8%), h1_dist=75 (~0.3%)
# DAX ~23000: proportions similaires, on garde les memes valeurs
# Point value: 1 Mini-DAX = 5 EUR/pt

BASE_PARAMS = dict(
    tp_points=300,
    trail_bars=20,
    max_sl_pts=200,
    max_trades_day=4,
    sma_period=20,
    start_offset_min=0,
    abs_start_hour=10,     # 10h CET (DAX ouvre a 9h, on attend 1h)
    abs_start_min=0,
    daily_loss_stop=3,
    daily_loss_usd=1000,
    pullback_bars=10,
    pullback_dist=15,
    min_h1_sma_dist=75,
)

# ============================================================
# 3) Backtest A: 5min (3 derniers mois - precis)
# ============================================================
print('\n' + '=' * 80)
print('  BACKTEST A: DAX 5min — {} -> {}'.format(df_5m.index.min().date(), df_5m.index.max().date()))
print('  MM20 Pullback | 1 Mini-DAX (5 EUR/pt) | 10h-17h25 CET')
print('=' * 80)

engine_5m = MM20BacktestEngine(**BASE_PARAMS, point_value=5.0)
report_5m = engine_5m.run(df_5m)

if report_5m and report_5m.total_trades > 0:
    trades_df = pd.DataFrame(report_5m.trades)
    trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
    trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')

    print('  Trades     : {}'.format(report_5m.total_trades))
    print('  Win Rate   : {}%'.format(report_5m.win_rate))
    print('  PnL        : EUR {:>+,.0f}'.format(report_5m.total_pnl_usd))
    print('  PF         : {}'.format(report_5m.profit_factor))
    print('  Avg Win    : EUR {:>+,.0f}'.format(report_5m.avg_win))
    print('  Avg Loss   : EUR {:>+,.0f}'.format(report_5m.avg_loss))
    print('  Max DD     : EUR {:>,.0f}'.format(report_5m.max_drawdown_usd))

    # Monthly breakdown
    print('\n  --- P&L Mensuel ---')
    for m, group in trades_df.groupby('month'):
        m_pnl = group['pnl_usd'].sum()
        m_trades = len(group)
        m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
        marker = '+' if m_pnl > 0 else '-'
        print('  {}  |  {:>2} trades  |  WR {:5.1f}%  |  EUR {:>+9,.0f}  {}'.format(
            m, m_trades, m_wr, m_pnl, marker))

    # Daily detail
    print('\n  --- Detail par jour ---')
    daily = trades_df.groupby('date').agg(
        trades=('pnl_usd', 'count'),
        pnl=('pnl_usd', 'sum'),
        wins=('pnl_usd', lambda x: (x > 0).sum()),
    ).reset_index()
    daily['month'] = pd.to_datetime(daily['date']).dt.to_period('M')

    for month, mgroup in daily.groupby('month'):
        month_pnl = 0
        print('\n  === {} ==='.format(month))
        for _, r in mgroup.iterrows():
            t = int(r['trades'])
            w = int(r['wins'])
            pnl = r['pnl']
            month_pnl += pnl
            bar = '+' * w + '-' * (t - w)
            marker = '+' if pnl > 0 else ('-' if pnl < 0 else '=')
            print('  {}  {:>2}t {:>4}  EUR {:>+8,.0f}  cumul {:>+8,.0f}  {}'.format(
                r['date'], t, bar, pnl, month_pnl, marker))

    # Trade detail
    print('\n  --- Tous les trades ---')
    for _, t in trades_df.iterrows():
        tag = '>>>' if t['pnl_usd'] > 0 else '   '
        print('  {} {} {:>5} {:.0f} @ {} -> {:.0f} @ {} {:10} {:>+6.0f}pts EUR {:>+,.0f}'.format(
            tag, t['date'], t['direction'],
            t['entry'], str(t['entry_time'])[-14:],
            t['exit'], str(t['exit_time'])[-14:],
            t['exit_reason'], t['pnl_pts'], t['pnl_usd']))
else:
    print('  AUCUN TRADE')
    # Debug
    print('\n  Debug: verification des conditions...')
    import pytz
    df_5m['sma20'] = df_5m['close'].rolling(20).mean()
    df_5m['paris'] = df_5m.index.tz_convert(PARIS)

    df_1h_for_debug = df_5m.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    df_1h_for_debug['sma20'] = df_1h_for_debug['close'].rolling(20).mean()

    df_5m['sma20_1h'] = df_1h_for_debug['sma20'].reindex(df_5m.index, method='ffill')
    df_5m['close_1h'] = df_1h_for_debug['close'].reindex(df_5m.index, method='ffill')
    df_5m['h1_dist'] = abs(df_5m['close_1h'] - df_5m['sma20_1h'])

    # Sample some bars in trading window
    trading = df_5m[(df_5m['paris'].dt.hour >= 10) & (df_5m['paris'].dt.hour <= 17)]
    sample_days = trading.groupby(trading['paris'].dt.date).first().head(5)

    for date, row in sample_days.iterrows():
        day_bars = trading[trading['paris'].dt.date == date]
        max_h1d = day_bars['h1_dist'].max()
        sides_m5 = (day_bars['close'] > day_bars['sma20']).sum()
        sides_h1 = (day_bars['close_1h'] > day_bars['sma20_1h']).sum()
        print('  {} | bars={} | max_h1_dist={:.0f} | M5 above SMA={} | H1 above SMA={}'.format(
            date, len(day_bars), max_h1d, sides_m5, sides_h1))

# ============================================================
# 4) Backtest B: 1h (6 mois complets)
# ============================================================
print('\n\n' + '=' * 80)
print('  BACKTEST B: DAX 1h — {} -> {}'.format(df_1h_full.index.min().date(), df_1h_full.index.max().date()))
print('  MM20 Pullback adapte 1h | 1 Mini-DAX (5 EUR/pt)')
print('  (trail_bars=4 en 1h ~ 20 bars en 5min, SMA20=20 barres 1h)')
print('=' * 80)

# Pour le 1h, on adapte trail_bars: 20 bars * 5min = 100min ~ 2 barres 1h
# Mais on garde un peu plus pour etre prudent: 4 barres 1h
engine_1h = MM20BacktestEngine(
    tp_points=300,
    trail_bars=4,          # 4 barres 1h ~ 4h lookback
    max_sl_pts=200,
    max_trades_day=4,
    sma_period=20,
    start_offset_min=0,
    abs_start_hour=10,
    abs_start_min=0,
    daily_loss_stop=3,
    point_value=5.0,
    daily_loss_usd=1000,
    pullback_bars=2,       # 2 barres 1h ~ 10 barres 5min
    pullback_dist=15,
    min_h1_sma_dist=75,
)

report_1h = engine_1h.run(df_1h_full, df_1h=df_daily)

if report_1h and report_1h.total_trades > 0:
    trades_1h = pd.DataFrame(report_1h.trades)
    trades_1h['date_parsed'] = pd.to_datetime(trades_1h['date'])
    trades_1h['month'] = trades_1h['date_parsed'].dt.to_period('M')

    print('  Trades     : {}'.format(report_1h.total_trades))
    print('  Win Rate   : {}%'.format(report_1h.win_rate))
    print('  PnL        : EUR {:>+,.0f}'.format(report_1h.total_pnl_usd))
    print('  PF         : {}'.format(report_1h.profit_factor))
    print('  Avg Win    : EUR {:>+,.0f}'.format(report_1h.avg_win))
    print('  Avg Loss   : EUR {:>+,.0f}'.format(report_1h.avg_loss))

    print('\n  --- P&L Mensuel ---')
    for m, group in trades_1h.groupby('month'):
        m_pnl = group['pnl_usd'].sum()
        m_trades = len(group)
        m_wr = len(group[group['pnl_usd'] > 0]) / m_trades * 100 if m_trades else 0
        marker = '+' if m_pnl > 0 else '-'
        print('  {}  |  {:>2} trades  |  WR {:5.1f}%  |  EUR {:>+9,.0f}  {}'.format(
            m, m_trades, m_wr, m_pnl, marker))

    # Daily detail
    daily_1h = trades_1h.groupby('date').agg(
        trades=('pnl_usd', 'count'),
        pnl=('pnl_usd', 'sum'),
        wins=('pnl_usd', lambda x: (x > 0).sum()),
    ).reset_index()
    daily_1h['month'] = pd.to_datetime(daily_1h['date']).dt.to_period('M')

    print('\n  --- Detail par jour ---')
    for month, mgroup in daily_1h.groupby('month'):
        month_pnl = 0
        print('\n  === {} ==='.format(month))
        for _, r in mgroup.iterrows():
            t_count = int(r['trades'])
            w = int(r['wins'])
            pnl = r['pnl']
            month_pnl += pnl
            bar = '+' * w + '-' * (t_count - w)
            marker = '+' if pnl > 0 else ('-' if pnl < 0 else '=')
            print('  {}  {:>2}t {:>4}  EUR {:>+8,.0f}  cumul {:>+8,.0f}  {}'.format(
                r['date'], t_count, bar, pnl, month_pnl, marker))
else:
    print('  AUCUN TRADE sur 1h')
    # Debug
    df_1h_full['sma20'] = df_1h_full['close'].rolling(20).mean()
    df_1h_full['paris'] = df_1h_full.index.tz_convert(PARIS)
    df_daily['sma20'] = df_daily['close'].rolling(20).mean()
    df_1h_full['sma20_d'] = df_daily['sma20'].reindex(df_1h_full.index, method='ffill')
    df_1h_full['close_d'] = df_daily['close'].reindex(df_1h_full.index, method='ffill')
    df_1h_full['h1_dist'] = abs(df_1h_full['close_d'] - df_1h_full['sma20_d'])

    trading = df_1h_full[(df_1h_full['paris'].dt.hour >= 10) & (df_1h_full['paris'].dt.hour <= 17)]
    print('\n  Debug: h1_dist stats in trading window:')
    print('    mean={:.0f} max={:.0f} min={:.0f}'.format(
        trading['h1_dist'].mean(), trading['h1_dist'].max(), trading['h1_dist'].min()))
    print('    bars with h1_dist>=75: {} / {}'.format(
        (trading['h1_dist'] >= 75).sum(), len(trading)))

print('\nResultats sauvegardes dans data/backtest_dax.txt')
