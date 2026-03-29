"""Backtest MM20 Pullback — P&L mensuel 2026 (Jan-Mar)."""
import asyncio
import os
import sys

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')

import pandas as pd
import numpy as np
from datetime import datetime
from backtester.mm20_engine import MM20BacktestEngine
from backtester.opr_engine import is_dst_gap
from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
import pytz
import httpx

PARIS = pytz.timezone('Europe/Paris')
POINT_VALUE = 8.0


def run_backtest(df_5m, df_1h=None):
    engine = MM20BacktestEngine(
        tp_points=300, trail_bars=20, max_sl_pts=200, max_trades_day=4,
        sma_period=20, start_offset_min=30, abs_start_hour=0,
        daily_loss_stop=3, point_value=POINT_VALUE, daily_loss_usd=1000,
        pullback_bars=10, pullback_dist=15, min_h1_sma_dist=75,
    )
    return engine.run(df_5m, df_1h=df_1h)


async def fetch_topstepx_bars(start_dt, end_dt):
    """Fetch bars from TopstepX API for dates not in Databento."""
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

    async def fetch_retry(label, **kwargs):
        for attempt in range(3):
            try:
                await asyncio.sleep(2)
                return await client.retrieve_bars(**kwargs)
            except Exception as e:
                print(f'  {label} attempt {attempt+1} failed: {e}')
                if attempt == 2:
                    raise
                await asyncio.sleep(5)

    bars_5m = await fetch_retry('5min',
        contractId=cid, live=False,
        startTime=start_dt, endTime=end_dt,
        unit=AggregationUnit.MINUTE, unitNumber=5,
        limit=500, includePartialBar=False,
    )

    bars_1h = await fetch_retry('1h',
        contractId=cid, live=False,
        startTime=datetime(start_dt.year, start_dt.month, start_dt.day - 5, 0, 0),
        endTime=end_dt,
        unit=AggregationUnit.HOUR, unitNumber=1,
        limit=200, includePartialBar=False,
    )

    await client.logout()

    def to_df(bars):
        rows = []
        for b in bars:
            ts = b['t']
            rows.append({
                'datetime': pd.Timestamp(ts, tz='UTC') if ('Z' in str(ts) or '+' in str(ts)) else pd.Timestamp(ts).tz_localize('UTC'),
                'open': float(b['o']), 'high': float(b['h']),
                'low': float(b['l']), 'close': float(b['c']),
                'volume': int(b['v']),
            })
        return pd.DataFrame(rows).set_index('datetime').sort_index()

    return to_df(bars_5m), to_df(bars_1h)


async def main():
    # 1) Load Databento 5y data (up to March 10)
    print('Loading Databento 5min data...')
    df_all = pd.read_csv('data/databento_nq_5min_5y.csv', index_col=0, parse_dates=True)
    if df_all.index.tz is None:
        df_all.index = df_all.index.tz_localize('UTC')
    print(f'  {len(df_all)} bars, up to {df_all.index.max()}')

    # 2) Fetch March 11 from TopstepX
    print('Fetching March 11 from TopstepX API...')
    df_api_5m, df_api_1h = await fetch_topstepx_bars(
        datetime(2026, 3, 11, 0, 0), datetime(2026, 3, 12, 0, 0)
    )
    print(f'  API 5min: {len(df_api_5m)} bars, API 1h: {len(df_api_1h)} bars')

    # 3) Combine: add March 11 5min bars to existing data
    df_combined = pd.concat([df_all, df_api_5m[~df_api_5m.index.isin(df_all.index)]])
    df_combined = df_combined.sort_index()

    # Filter 2026 only (with Dec 2025 lookback for SMA)
    df_2026 = df_combined[df_combined.index >= '2025-12-15']
    print(f'Data for backtest: {len(df_2026)} bars, {df_2026.index.min()} -> {df_2026.index.max()}')

    # 4) Run backtest on full 2026 period
    print('\nRunning MM20 Pullback backtest...')
    report = run_backtest(df_2026)

    if not report or report.total_trades == 0:
        print('Aucun trade en 2026!')
        return

    # 5) Monthly breakdown
    trades_df = pd.DataFrame(report.trades)
    trades_df['date_parsed'] = pd.to_datetime(trades_df['date'])
    trades_df['month'] = trades_df['date_parsed'].dt.to_period('M')

    # Filter only 2026
    trades_df = trades_df[trades_df['date_parsed'] >= '2026-01-01']

    print()
    print('=' * 65)
    print('  MM20 PULLBACK — P&L MENSUEL 2026')
    print('  Instrument: 4 MNQ ($8/pt) | Horaires: 15h/16h-19h39/20h39')
    print('=' * 65)

    total_pnl = 0
    total_trades = 0

    for m, group in trades_df.groupby('month'):
        m_pnl = group['pnl_usd'].sum()
        m_trades = len(group)
        m_wins = len(group[group['pnl_usd'] > 0])
        m_wr = m_wins / m_trades * 100 if m_trades else 0
        m_losses = [p for p in group['pnl_usd'] if p < 0]
        m_wins_v = [p for p in group['pnl_usd'] if p > 0]
        m_pf = abs(sum(m_wins_v) / sum(m_losses)) if m_losses and sum(m_losses) != 0 else 99.0
        total_pnl += m_pnl
        total_trades += m_trades
        marker = '+' if m_pnl > 0 else '-'
        print(f'  {m}  |  {m_trades:>2} trades  |  WR {m_wr:5.1f}%  |  PF {m_pf:5.2f}  |  PnL ${m_pnl:>+9,.0f}  {marker}')

    print(f'  {"-" * 55}')
    overall_wr = len(trades_df[trades_df['pnl_usd'] > 0]) / len(trades_df) * 100 if len(trades_df) else 0
    print(f'  TOTAL  |  {total_trades:>2} trades  |  WR {overall_wr:5.1f}%  |             PnL ${total_pnl:>+9,.0f}')

    # 6) Daily detail per month
    for m, group in trades_df.groupby('month'):
        print(f'\n  --- Detail {m} ---')
        for _, t in group.iterrows():
            d = t['direction']
            ep = t['entry']
            xp = t['exit']
            xr = t['exit_reason']
            pnl = t['pnl_usd']
            pts = t['pnl_pts']
            et = str(t['entry_time'])
            xt = str(t['exit_time'])
            print(f'    {t["date"]} {d:>5} | {ep:.0f} @ {et[-14:]} -> {xp:.0f} @ {xt[-14:]} ({xr:10}) | {pts:+6.0f} pts | ${pnl:+,.0f}')


asyncio.run(main())
