"""Backtest MM20 Pullback sur le 11 mars 2026 avec données TopstepX API."""
import asyncio
import os
import sys

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')

import pandas as pd
import numpy as np
from datetime import datetime
from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
from backtester.mm20_engine import MM20BacktestEngine
from backtester.opr_engine import is_dst_gap
import pytz
import httpx

PARIS = pytz.timezone('Europe/Paris')


def to_df(bars):
    rows = []
    for b in bars:
        ts = b.get('t')
        rows.append({
            'datetime': pd.Timestamp(ts, tz='UTC') if ('Z' in str(ts) or '+' in str(ts)) else pd.Timestamp(ts).tz_localize('UTC'),
            'open': float(b['o']),
            'high': float(b['h']),
            'low': float(b['l']),
            'close': float(b['c']),
            'volume': int(b['v']),
        })
    return pd.DataFrame(rows).set_index('datetime').sort_index()


async def main():
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
    print('Login OK')

    contracts = await client.search_for_contracts(searchText='MNQ', live=False)
    cid = contracts[0]['id']
    print(f'Contract: {cid}')

    # Helper with retry
    async def fetch_with_retry(label, **kwargs):
        for attempt in range(3):
            try:
                print(f'Fetching {label} (attempt {attempt+1})...')
                await asyncio.sleep(2)
                return await client.retrieve_bars(**kwargs)
            except Exception as e:
                print(f'  Error: {e}')
                if attempt == 2:
                    raise
                await asyncio.sleep(5)

    # Fetch 5min bars (March 10-11 for SMA20 lookback)
    bars_5m = await fetch_with_retry('5min bars',
        contractId=cid, live=False,
        startTime=datetime(2026, 3, 10, 8, 0),
        endTime=datetime(2026, 3, 12, 0, 0),
        unit=AggregationUnit.MINUTE, unitNumber=5,
        limit=500, includePartialBar=False,
    )
    print(f'  -> {len(bars_5m)} bars')

    # Fetch 1h bars (March 7-11 for SMA20 H1)
    bars_1h = await fetch_with_retry('1h bars',
        contractId=cid, live=False,
        startTime=datetime(2026, 3, 7, 0, 0),
        endTime=datetime(2026, 3, 12, 0, 0),
        unit=AggregationUnit.HOUR, unitNumber=1,
        limit=200, includePartialBar=False,
    )
    print(f'  -> {len(bars_1h)} bars')

    await client.logout()

    df_5m = to_df(bars_5m)
    df_1h = to_df(bars_1h)
    print(f'5min: {len(df_5m)} bars | {df_5m.index.min()} -> {df_5m.index.max()}')
    print(f'1h:   {len(df_1h)} bars | {df_1h.index.min()} -> {df_1h.index.max()}')

    # Run MM20 Pullback (mêmes paramètres que la prod)
    engine = MM20BacktestEngine(
        tp_points=300,
        trail_bars=20,
        max_sl_pts=200,
        max_trades_day=4,
        sma_period=20,
        start_offset_min=30,
        abs_start_hour=0,
        daily_loss_stop=3,
        point_value=8.0,
        daily_loss_usd=1000,
        pullback_bars=10,
        pullback_dist=15,
        min_h1_sma_dist=75,
    )
    report = engine.run(df_5m, df_1h=df_1h)

    if not report or report.total_trades == 0:
        print('\n=== AUCUN TRADE LE 11 MARS 2026 ===')
        # Debug
        df_5m['sma20'] = df_5m['close'].rolling(20).mean()
        df_5m['paris'] = df_5m.index.tz_convert(PARIS)
        df_1h['sma20'] = df_1h['close'].rolling(20).mean()

        march11 = df_5m[df_5m['paris'].dt.date == pd.Timestamp('2026-03-11').date()].copy()
        march11['sma20_1h'] = df_1h['sma20'].reindex(df_5m.index, method='ffill').reindex(march11.index)
        march11['close_1h'] = df_1h['close'].reindex(df_5m.index, method='ffill').reindex(march11.index)
        march11['h1_dist'] = abs(march11['close_1h'] - march11['sma20_1h'])

        trading = march11[(march11['paris'].dt.hour >= 14) & (march11['paris'].dt.hour <= 20)]
        print(f'\nBarres 14h-20h ({len(trading)} bars):')
        for _, r in trading.iterrows():
            h1d = r['h1_dist']
            s5 = 'L' if r['close'] > r['sma20'] else 'S'
            s1 = 'L' if r['close_1h'] > r['sma20_1h'] else 'S'
            h1ok = 'v' if h1d >= 75 else f'x({h1d:.0f})'
            idx_pos = march11.index.get_loc(r.name)
            lb = march11.iloc[max(0, idx_pos - 10):idx_pos]
            sma = r['sma20']
            if len(lb) > 0 and not pd.isna(sma):
                pbd = (lb['low'] - sma).abs().min() if s5 == 'L' else (lb['high'] - sma).abs().min()
                pbok = 'v' if pbd <= 15 else f'x({pbd:.0f})'
            else:
                pbok = 'na'
            print(f'  {r["paris"].strftime("%H:%M")} c={r["close"]:.0f} sma={r["sma20"]:.0f} | H1d={h1d:.0f}{h1ok} | {s5}/{s1} | pb={pbok}')
        return

    # Display results
    print()
    print('=' * 60)
    print('  BACKTEST MM20 PULLBACK — 11 MARS 2026')
    print('  DST gap: True -> horaires 15h00-19h39 Paris')
    print('  Instrument: 4 MNQ ($8/pt)')
    print('=' * 60)
    print(f'  Trades     : {report.total_trades}')
    print(f'  Win Rate   : {report.win_rate}%')
    print(f'  PnL        : ${report.total_pnl_usd:+,.0f}')
    print(f'  PF         : {report.profit_factor}')
    print(f'  Avg Win    : ${report.avg_win:+,.0f}')
    print(f'  Avg Loss   : ${report.avg_loss:+,.0f}')
    print()

    # Detail des trades (report.trades are dicts with 'entry'/'exit' keys)
    for i, t in enumerate(report.trades):
        d = t['direction']
        ep = t['entry']
        et_str = str(t['entry_time'])
        xp = t['exit']
        xt_str = str(t['exit_time'])
        xr = t['exit_reason']
        pnl = t['pnl_usd']
        pts = t['pnl_pts']
        date = t['date'] if isinstance(t, dict) else t.date
        print(f'  Trade #{i+1}: {date} {d:>5} | entry {ep:.2f} @ {et_str[-14:]} -> exit {xp:.2f} @ {xt_str[-14:]} ({xr}) | {pts:+.0f} pts | ${pnl:+,.0f}')


asyncio.run(main())
