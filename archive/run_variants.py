"""Compare trailing variants on real NQ data."""
import asyncio
import sys
import json
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()


async def test_variants():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.engine import BacktestEngine
    from dataclasses import asdict
    import pandas as pd
    from datetime import datetime, timedelta
    from pathlib import Path

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )

    print('Connexion ProjectX...')
    client = ProjectXClient(TOPSTEPX_URLS)
    await client.login({
        'auth_type': 'api_key',
        'userName': os.getenv('PROJECTX_USERNAME'),
        'apiKey': os.getenv('PROJECTX_API_KEY'),
    })
    print('Connecte OK')

    contracts = await client.search_for_contracts(searchText='NQ', live=False)
    contract_id = contracts[0]['id']

    now = datetime.utcnow()
    all_1min = []
    all_5min = []
    n_chunks = 5

    for chunk_i in range(n_chunks):
        chunk_end = now - timedelta(days=chunk_i * 7)
        chunk_start = chunk_end - timedelta(days=7)
        print(f'  Chunk {chunk_i+1}/{n_chunks}: {chunk_start.date()} -> {chunk_end.date()}')

        try:
            bars = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=1,
                limit=10000, includePartialBar=False
            )
            if bars:
                all_1min.extend(bars)
        except Exception as e:
            print(f'    1min err: {e}')

        try:
            bars5 = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=10000, includePartialBar=False
            )
            if bars5:
                all_5min.extend(bars5)
        except Exception as e:
            print(f'    5min err: {e}')

    def to_df(bars):
        data = []
        for b in bars:
            d = b if isinstance(b, dict) else b.__dict__
            data.append({
                'datetime': d.get('t') or d.get('datetime') or d.get('timestamp'),
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
    print(f'\n{len(df_1min)} barres 1min, {len(df_5min)} barres 5min')

    options = {'hvl': 24820, 'call_wall': 24900, 'put_wall': 24000}

    variants = {
        'BASE (trail=3)': {
            'stop_fb': 8, 'stop_br': 2,
            'trail_step': 3, 'exit_fb': 'vpoc'
        },
        'VARIANTE A (trail=5)': {
            'stop_fb': 8, 'stop_br': 2,
            'trail_step': 5, 'exit_fb': 'vpoc'
        },
        'VARIANTE B (stop fixe)': {
            'stop_fb': 8, 'stop_br': 2,
            'trail_step': 0, 'exit_fb': 'vpoc'
        },
        'VARIANTE C (trail=3 both)': {
            'stop_fb': 8, 'stop_br': 2,
            'trail_step': 3, 'exit_fb': 'both'
        },
        'VARIANTE D (trail=5 both)': {
            'stop_fb': 8, 'stop_br': 2,
            'trail_step': 5, 'exit_fb': 'both'
        },
        'VARIANTE E (fixe both)': {
            'stop_fb': 8, 'stop_br': 2,
            'trail_step': 0, 'exit_fb': 'both'
        },
    }

    print()
    print('=== COMPARAISON VARIANTES ===')
    print()
    header = (
        f'{"Variante":<28} '
        f'{"Trades":>6} '
        f'{"WR":>7} '
        f'{"PF":>6} '
        f'{"PnL":>9} '
        f'{"DD":>8} '
        f'{"Sharpe":>7} '
        f'{"Expect":>8} '
        f'{"VPOC":>5} '
        f'{"MM20":>5} '
        f'{"Stop":>5} '
        f'{"Sess":>5}'
    )
    print(header)
    print('-' * len(header))

    best_score = -999
    best_name = ''
    best_params = {}
    all_results = []

    for name, params in variants.items():
        engine = BacktestEngine(params=params)
        r = engine.run(df_1min, df_5min, options)
        if r:
            score = r.profit_factor * 0.4 + r.sharpe_ratio * 0.3 + r.win_rate / 100 * 0.2 - r.max_drawdown / 10000 * 0.1
            marker = ''
            if score > best_score:
                best_score = score
                best_name = name
                best_params = params
                marker = ' <--'
            print(
                f'{name:<28} '
                f'{r.total_trades:>6} '
                f'{r.win_rate:>6.1f}% '
                f'{r.profit_factor:>6.2f} '
                f'${r.total_pnl_dollars:>8.0f} '
                f'${r.max_drawdown:>7.0f} '
                f'{r.sharpe_ratio:>7.2f} '
                f'${r.expectancy:>7.2f} '
                f'{r.exits_vpoc:>5} '
                f'{r.exits_mm20:>5} '
                f'{r.exits_stop:>5} '
                f'{r.exits_session:>5}'
                f'{marker}'
            )
            all_results.append({
                'name': name, 'params': params,
                'trades': r.total_trades, 'wr': r.win_rate,
                'pf': r.profit_factor, 'pnl': r.total_pnl_dollars,
                'dd': r.max_drawdown, 'sharpe': r.sharpe_ratio,
                'vpoc': r.exits_vpoc, 'mm20': r.exits_mm20,
                'stop': r.exits_stop, 'session': r.exits_session,
                'score': round(score, 4),
            })

    print()
    print(f'MEILLEURE : {best_name} (score={best_score:.4f})')
    print(f'Params    : {best_params}')

    # Save best variant
    engine = BacktestEngine(params=best_params)
    report = engine.run(df_1min, df_5min, options)
    if report:
        rd = asdict(report)
        rd['best_params'] = best_params
        rd['variant_name'] = best_name
        Path('data').mkdir(exist_ok=True)
        Path('data/last_backtest.json').write_text(
            json.dumps(rd, default=str)
        )
        print('Rapport mis a jour -> data/last_backtest.json')

        # Detail exits for best
        print()
        print(f'=== DETAIL BEST: {best_name} ===')
        print(f'  Exits stop    : {report.exits_stop}')
        print(f'  Exits VPOC    : {report.exits_vpoc}')
        print(f'  Exits MM20    : {report.exits_mm20}')
        print(f'  Exits session : {report.exits_session}')
        print()
        print('10 derniers trades:')
        for t in report.trades[-10:]:
            pnl = t['pnl_dollars']
            sign = '+' if pnl > 0 else ''
            print(
                f'  {t["direction"]:5} {t["strategy"]:15} '
                f'PnL:{sign}${pnl:7.0f} '
                f'Exit:{t["exit_reason"]:12} '
                f'Bars:{t["bars_held"]}'
            )

    await client.logout()
    print('\nTermine.')


asyncio.run(test_variants())
