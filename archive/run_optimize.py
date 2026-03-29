"""Run full optimization on real NQ data (30 days, 400 combos)."""
import asyncio
import sys
import json
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()


async def optimize():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.engine import BacktestEngine, BacktestOptimizer
    import pandas as pd
    from datetime import datetime, timedelta
    from pathlib import Path
    from dataclasses import asdict

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
    print(f'Contract NQ id={contract_id}')

    # Fetch 30 days in 7-day reverse chunks (naive UTC datetimes)
    now = datetime.utcnow()
    all_1min = []
    all_5min = []
    n_chunks = 5  # 5 x 7 = 35 days

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
                print(f'    1min: +{len(bars)} barres')
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
                print(f'    5min: +{len(bars5)} barres')
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
    print(f'\nTotal 1min: {len(df_1min)} barres')
    print(f'Total 5min: {len(df_5min)} barres')
    print()

    # Options levels (current approximate NQ levels)
    options = {'hvl': 24820, 'call_wall': 24900, 'put_wall': 24000}

    print('=== OPTIMISATION 400 COMBINAISONS ===')
    print()

    opt = BacktestOptimizer()

    def on_progress(current, total):
        if current % 50 == 0:
            print(f'  {current}/{total} combinaisons...')

    results = opt.run_optimization(
        df_1min, df_5min, options,
        min_trades=5, progress_callback=on_progress
    )

    print(f'\nCombos valides: {len(results)}')
    print()

    if results:
        print('=== TOP 10 RESULTATS ===')
        print()
        for i, r in enumerate(results[:10]):
            p = r['params']
            print(
                f"{i+1:2}. "
                f"PF:{r['profit_factor']:5.2f} "
                f"WR:{r['win_rate']:5.1f}% "
                f"PnL:${r['pnl']:8.0f} "
                f"DD:${r['max_drawdown']:7.0f} "
                f"Sharpe:{r['sharpe']:4.2f} "
                f"| fb:{p['stop_fb']}pt "
                f"br:{p['stop_br']}pt "
                f"trail:{p['trail_step']}pt "
                f"exit:{p['exit_fb']}"
            )

        print()
        best = results[0]
        bp = best['params']
        print('=== MEILLEURE COMBINAISON ===')
        print(f"  Stop Fake Breakout : {bp['stop_fb']} pts (= ${bp['stop_fb']*20})")
        print(f"  Stop Breakout      : {bp['stop_br']} pts (= ${bp['stop_br']*20})")
        ts = bp['trail_step']
        print(f"  Trailing Step      : {ts} pts" + (" (STOP FIXE)" if ts == 0 else ""))
        print(f"  Exit Fake Breakout : {bp['exit_fb']}")
        print()
        print(f"  Profit Factor      : {best['profit_factor']}")
        print(f"  Win Rate           : {best['win_rate']}%")
        print(f"  P&L Total          : ${best['pnl']:.0f}")
        print(f"  Max Drawdown       : ${best['max_drawdown']:.0f}")
        print(f"  Expectancy         : ${best['expectancy']:.2f}/trade")
        print(f"  Sharpe             : {best['sharpe']}")
        print()

        # Detailed backtest with best params
        print('Backtest detaille meilleurs params...')
        engine = BacktestEngine(params=bp)
        report = engine.run(df_1min, df_5min, options)

        if report:
            print()
            print('=== DETAIL EXITS ===')
            print(f'  Stop trailing : {report.exits_stop}')
            print(f'  MM20 5min     : {report.exits_mm20}')
            print(f'  VPOC          : {report.exits_vpoc}')
            print(f'  Session end   : {report.exits_session}')
            print()
            print('=== PAR STRATEGIE ===')
            print(f'  Fake Breakout : {report.fake_breakout_trades} trades / {report.fake_breakout_winrate}% WR')
            print(f'  Breakout reel : {report.breakout_trades} trades / {report.breakout_winrate}% WR')
            print()
            print('=== 10 DERNIERS TRADES ===')
            for t in report.trades[-10:]:
                pnl = t['pnl_dollars']
                sign = '+' if pnl > 0 else ''
                print(
                    f"  {t['direction']:5} {t['strategy']:15} "
                    f"PnL:{sign}${pnl:7.0f} "
                    f"Exit:{t['exit_reason']:12} "
                    f"Bars:{t['bars_held']}"
                )

        # Save for dashboard
        Path('data').mkdir(exist_ok=True)
        Path('data/optimization.json').write_text(
            json.dumps(results[:10], default=str)
        )
        if report:
            rd = asdict(report)
            rd['best_params'] = bp
            Path('data/last_backtest.json').write_text(
                json.dumps(rd, default=str)
            )
        print()
        print('Resultats sauvegardes:')
        print('  -> data/optimization.json')
        print('  -> data/last_backtest.json')
    else:
        print('Aucune combinaison valide (min_trades=5)')
        print('Les regles Dalton sont strictes sur ces donnees')

    await client.logout()
    print('\nTermine.')


asyncio.run(optimize())
