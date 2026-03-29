"""Quick optimization with previous-day VA engine."""
import asyncio
import sys
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.engine import BacktestEngine
    import pandas as pd
    from datetime import datetime, timedelta
    import itertools

    DAYS = 120

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    print('Connexion...')
    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id']

    now = datetime.utcnow()
    all_1min = []
    all_5min = []
    n_chunks = (DAYS // 7) + 1

    print(f'Fetching {DAYS}j data...')
    for chunk_i in range(n_chunks):
        chunk_end = now - timedelta(days=chunk_i * 7)
        chunk_start = chunk_end - timedelta(days=7)
        try:
            bars = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=1,
                limit=10000, includePartialBar=False
            )
            if bars:
                all_1min.extend(bars)
        except:
            pass
        try:
            bars5 = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=10000, includePartialBar=False
            )
            if bars5:
                all_5min.extend(bars5)
        except:
            pass

    def to_df(bars):
        data = []
        for b in bars:
            d = b if isinstance(b, dict) else b.__dict__
            data.append({
                'datetime': d.get('t') or d.get('datetime'),
                'open': float(d.get('o') or d.get('open') or 0),
                'high': float(d.get('h') or d.get('high') or 0),
                'low': float(d.get('l') or d.get('low') or 0),
                'close': float(d.get('c') or d.get('close') or 0),
                'volume': float(d.get('v') or d.get('volume') or 1),
            })
        df = pd.DataFrame(data)
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
        df = df.sort_values('datetime').drop_duplicates('datetime')
        return df.set_index('datetime')

    df_1min = to_df(all_1min)
    df_5min = to_df(all_5min)
    print(f'1min: {len(df_1min)}, 5min: {len(df_5min)}')

    options = {'hvl': 24820, 'call_wall': 24900, 'put_wall': 24000}

    # Grid search
    grid = {
        'stop_fb': [20, 25, 30, 35, 40],
        'stop_br': [15, 20, 25, 30],
        'trail_fb': [0],
        'trail_br': [0, 10, 15, 20],
        'exit_fb': ['vpoc', 'both'],
    }

    combos = list(itertools.product(
        grid['stop_fb'], grid['stop_br'], grid['trail_fb'],
        grid['trail_br'], grid['exit_fb']
    ))
    print(f'\n{len(combos)} combinaisons a tester...\n')

    results = []
    for idx, (sfb, sbr, tfb, tbr, efb) in enumerate(combos):
        params = {
            'stop_fb': sfb, 'stop_br': sbr,
            'trail_fb': tfb, 'trail_br': tbr,
            'exit_fb': efb,
        }
        engine = BacktestEngine(params=params)
        report = engine.run(
            df_1min, df_5min, options,
            daily_loss_limit=-900,
            max_trades_per_day=2,
        )
        if report and report.total_trades >= 15:
            pf = min(report.profit_factor, 10.0)
            score = (
                pf * 0.4 +
                report.sharpe_ratio * 0.3 +
                min(report.win_rate / 100, 1.0) * 0.2 -
                (report.max_drawdown / 10000) * 0.1
            )
            results.append({
                'params': params,
                'trades': report.total_trades,
                'wr': report.win_rate,
                'pnl': report.total_pnl_dollars,
                'pf': pf,
                'dd': report.max_drawdown,
                'exp': report.expectancy,
                'sharpe': report.sharpe_ratio,
                'fb_trades': report.fake_breakout_trades,
                'fb_wr': report.fake_breakout_winrate,
                'br_trades': report.breakout_trades,
                'br_wr': report.breakout_winrate,
                'score': round(score, 4),
            })

        if (idx + 1) % 20 == 0:
            print(f'  {idx+1}/{len(combos)}...')

    results.sort(key=lambda x: x['score'], reverse=True)

    print()
    print('=' * 70)
    print(f'TOP 15 RESULTATS (sur {len(results)} valides)')
    print('=' * 70)
    for i, r in enumerate(results[:15]):
        p = r['params']
        print(
            f"#{i+1:2d} | sfb={p['stop_fb']:2d} sbr={p['stop_br']:2d} "
            f"tfb={p['trail_fb']:2d} tbr={p['trail_br']:2d} exit={p['exit_fb']:4s} | "
            f"T={r['trades']:2d} WR={r['wr']:4.1f}% PnL=${r['pnl']:+8.0f} "
            f"PF={r['pf']:.2f} DD=${r['dd']:6.0f} "
            f"FB={r['fb_trades']}({r['fb_wr']:.0f}%) BR={r['br_trades']}({r['br_wr']:.0f}%) "
            f"S={r['score']:.3f}"
        )

    print()
    print('=' * 70)
    print('5 PIRES RESULTATS')
    print('=' * 70)
    for i, r in enumerate(results[-5:]):
        p = r['params']
        print(
            f"  | sfb={p['stop_fb']:2d} sbr={p['stop_br']:2d} "
            f"tfb={p['trail_fb']:2d} tbr={p['trail_br']:2d} exit={p['exit_fb']:4s} | "
            f"T={r['trades']:2d} WR={r['wr']:4.1f}% PnL=${r['pnl']:+8.0f} "
            f"PF={r['pf']:.2f}"
        )

    await client.logout()
    print('\nTermine.')


asyncio.run(run())
