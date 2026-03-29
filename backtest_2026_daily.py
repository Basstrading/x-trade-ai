"""Backtest MM20 Pullback - Detail journalier 2026."""
import asyncio, os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')
import pandas as pd
import numpy as np
from datetime import datetime
from backtester.mm20_engine import MM20BacktestEngine
from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
import httpx


async def main():
    # Load Databento
    df_all = pd.read_csv('data/databento_nq_5min_5y.csv', index_col=0, parse_dates=True)
    if df_all.index.tz is None:
        df_all.index = df_all.index.tz_localize('UTC')

    # Fetch March 11 from TopstepX
    urls = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )
    client = ProjectXClient(urls)
    client.session.timeout = httpx.Timeout(120.0)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })
    contracts = await client.search_for_contracts(searchText='MNQ', live=False)
    cid = contracts[0]['id']

    bars = []
    for attempt in range(5):
        try:
            await asyncio.sleep(3)
            bars = await client.retrieve_bars(
                contractId=cid, live=False,
                startTime=datetime(2026, 3, 11, 0, 0),
                endTime=datetime(2026, 3, 12, 0, 0),
                unit=AggregationUnit.MINUTE, unitNumber=5, limit=500, includePartialBar=False,
            )
            if bars:
                break
        except Exception as e:
            print('  API retry {}/5: {}'.format(attempt + 1, e))
            await asyncio.sleep(5)
    await client.logout()
    print('  March 11 API bars: {}'.format(len(bars)))

    if bars:
        rows = []
        for b in bars:
            rows.append({
                'datetime': pd.Timestamp(b['t'], tz='UTC'),
                'open': float(b['o']), 'high': float(b['h']),
                'low': float(b['l']), 'close': float(b['c']),
                'volume': int(b['v']),
            })
        df_api = pd.DataFrame(rows).set_index('datetime').sort_index()
        df_combined = pd.concat([df_all, df_api[~df_api.index.isin(df_all.index)]]).sort_index()
    else:
        print('  WARNING: no API bars, using Databento only (up to March 10)')
        df_combined = df_all
    df_2026 = df_combined[df_combined.index >= '2025-12-15']

    # Run backtest
    engine = MM20BacktestEngine(
        tp_points=300, trail_bars=20, max_sl_pts=200, max_trades_day=4,
        sma_period=20, start_offset_min=30, abs_start_hour=0,
        daily_loss_stop=3, point_value=8.0, daily_loss_usd=1000,
        pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
    )
    report = engine.run(df_2026)

    trades_df = pd.DataFrame(report.trades)
    trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
    trades_df = trades_df[trades_df['date_parsed'] >= '2026-01-01']

    # Daily P&L
    daily = trades_df.groupby('date').agg(
        trades=('pnl_usd', 'count'),
        pnl=('pnl_usd', 'sum'),
        wins=('pnl_usd', lambda x: (x > 0).sum()),
    ).reset_index()
    daily['date_parsed'] = pd.to_datetime(daily['date'])
    daily['month'] = daily['date_parsed'].dt.to_period('M')

    all_dates = pd.bdate_range('2026-01-01', '2026-03-11', freq='B')

    months = sorted(daily['month'].unique())

    for month in months:
        month_days = [d for d in all_dates if d.to_period('M') == month]
        mgroup = daily[daily['month'] == month]
        month_pnl = 0

        print('=' * 90)
        print('  {}'.format(month))
        print('=' * 90)

        for d in month_days:
            d_str = d.strftime('%Y-%m-%d')
            day_trades = trades_df[trades_df['date'] == d_str]
            if len(day_trades) == 0:
                continue
            day_pnl = day_trades['pnl_usd'].sum()
            month_pnl += day_pnl
            n = len(day_trades)
            w = int((day_trades['pnl_usd'] > 0).sum())
            marker = '+' if day_pnl > 0 else ('-' if day_pnl < 0 else '=')
            print('')
            print('  {} | {}t ({}W/{}L) | jour {:>+,.0f} | mois {:>+,.0f}  {}'.format(
                d_str, n, w, n - w, day_pnl, month_pnl, marker))
            for _, t in day_trades.iterrows():
                d_dir = t['direction']
                ep = t['entry']
                xp = t['exit']
                xr = t['exit_reason']
                pnl = t['pnl_usd']
                pts = t['pnl_pts']
                et = str(t['entry_time'])[-14:]
                xt = str(t['exit_time'])[-14:]
                tag = '>>>' if pnl > 0 else '   '
                print('    {} {:>5} {:.0f} @ {} -> {:.0f} @ {} {:10} {:>+6.0f}pts {:>+,.0f}'.format(
                    tag, d_dir, ep, et, xp, xt, xr, pts, pnl))

        t_trades = int(mgroup['trades'].sum())
        t_wins = int(mgroup['wins'].sum())
        t_losses = t_trades - t_wins
        t_wr = t_wins / t_trades * 100 if t_trades else 0
        days_p = int((mgroup['pnl'] > 0).sum())
        days_n = int((mgroup['pnl'] < 0).sum())
        print('')
        print('  ' + '-' * 80)
        print('  TOTAL {}  |  {}t ({}W/{}L) WR {:.0f}%  |  {}j+ / {}j-  |  PnL {:>+,.0f}'.format(
            month, t_trades, t_wins, t_losses, t_wr, days_p, days_n, month_pnl))
        print('')

    # Grand total
    total_pnl = daily['pnl'].sum()
    total_t = int(daily['trades'].sum())
    total_w = int(daily['wins'].sum())
    days_pos = int((daily['pnl'] > 0).sum())
    days_neg = int((daily['pnl'] < 0).sum())
    print('=' * 90)
    print('  TOTAL 2026 (au 11/03)')
    print('  {} trades | WR {:.0f}% | {}j+ / {}j- | PnL {:>+,.0f}'.format(
        total_t, total_w / total_t * 100, days_pos, days_neg, total_pnl))
    print('=' * 90)


asyncio.run(main())
