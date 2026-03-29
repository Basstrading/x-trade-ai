"""Optimise les ratios de stops proportionnels à l'IB."""
import asyncio
import sys
import os
import json
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv()

# Disable debug logs for speed
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

PARAM_GRID = {
    'stop_ratio_trend':   [0.06, 0.08, 0.10, 0.12],
    'stop_ratio_normal':  [0.06, 0.08, 0.10],
    'stop_ratio_neutral': [0.04, 0.06, 0.08],
    'trail_ratio_trend':  [0.03, 0.04, 0.05],
}


async def optimize():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.engine import DaltonEngine
    import pandas as pd
    from datetime import datetime, timedelta
    from pathlib import Path

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

    df1 = to_df(all_1min)
    df5 = to_df(all_5min)
    options = {'hvl': 24820, 'call_wall': 24900, 'put_wall': 24000}

    print(f'1min: {len(df1)}, 5min: {len(df5)}')

    keys = list(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combos = list(itertools.product(*values))
    print(f'\nOptimisation : {len(combos)} combos\n')

    results = []
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        engine = DaltonEngine(params=params)
        r = engine.run(df1, df5, options, daily_loss_limit=-900, max_trades_per_day=4)

        if r and r.total_trades >= 15:
            pf = min(r.profit_factor, 10.0)
            score = (
                pf * 0.35
                + (r.win_rate / 100) * 0.25
                + min(r.sharpe_ratio, 3) / 3 * 0.25
                - (r.max_drawdown / 5000) * 0.15
            )
            results.append({
                'params': params,
                'trades': r.total_trades,
                'win_rate': r.win_rate,
                'pnl': r.total_pnl_dollars,
                'pf': round(pf, 2),
                'sharpe': r.sharpe_ratio,
                'max_dd': r.max_drawdown,
                'jours_limit': r.days_over_agent_limit,
                'proj_month': r.projected_monthly,
                'score': round(score, 4),
                'by_signal': r.winrate_by_signal,
                'by_daytype': r.winrate_by_daytype,
            })

        if (idx + 1) % 20 == 0:
            print(f'  {idx+1}/{len(combos)}...')

    results.sort(key=lambda x: x['score'], reverse=True)

    print()
    print(f'Combos valides : {len(results)} / {len(combos)}')
    print()
    print('=' * 80)
    print('TOP 10 RESULTATS')
    print('=' * 80)
    print(
        f'{"#":>2} {"PF":>5} {"WR":>6} {"PnL":>8} {"DD":>7} {"J>lim":>5} '
        f'{"Proj/m":>8} {"stop_t":>6} {"stop_n":>6} {"stop_neu":>8} {"trail_t":>7} {"Score":>6}'
    )
    print('-' * 80)

    for i, r in enumerate(results[:10]):
        p = r['params']
        print(
            f'{i+1:>2} {r["pf"]:>5.2f} {r["win_rate"]:>5.1f}% '
            f'${r["pnl"]:>7.0f} ${r["max_dd"]:>6.0f} {r["jours_limit"]:>5} '
            f'${r["proj_month"]:>7.0f} '
            f'{p["stop_ratio_trend"]:>6.2f} {p["stop_ratio_normal"]:>6.2f} '
            f'{p["stop_ratio_neutral"]:>8.2f} {p["trail_ratio_trend"]:>7.2f} '
            f'{r["score"]:>6.3f}'
        )

    print()
    print('5 PIRES :')
    for i, r in enumerate(results[-5:]):
        p = r['params']
        print(
            f'   {r["pf"]:>5.2f} {r["win_rate"]:>5.1f}% '
            f'${r["pnl"]:>7.0f} ${r["max_dd"]:>6.0f} '
            f'{p["stop_ratio_trend"]:>6.2f} {p["stop_ratio_normal"]:>6.2f} '
            f'{p["stop_ratio_neutral"]:>8.2f} {p["trail_ratio_trend"]:>7.2f}'
        )

    if results:
        best = results[0]
        bp = best['params']
        ib_ex = 250
        print()
        print('=' * 80)
        print('MEILLEURE COMBINAISON')
        print('=' * 80)
        print(f'stop_ratio_trend   : {bp["stop_ratio_trend"]} -> {bp["stop_ratio_trend"]*ib_ex:.0f}pts sur IB 250pts')
        print(f'stop_ratio_normal  : {bp["stop_ratio_normal"]} -> {bp["stop_ratio_normal"]*ib_ex:.0f}pts')
        print(f'stop_ratio_neutral : {bp["stop_ratio_neutral"]} -> {bp["stop_ratio_neutral"]*ib_ex:.0f}pts')
        print(f'trail_ratio_trend  : {bp["trail_ratio_trend"]} -> {bp["trail_ratio_trend"]*ib_ex:.0f}pts')
        print()
        print(f'PF       : {best["pf"]}')
        print(f'Win Rate : {best["win_rate"]}%')
        print(f'P&L 120j : ${best["pnl"]:.0f}')
        print(f'Max DD   : ${best["max_dd"]:.0f}')
        print(f'Proj/mois: ${best["proj_month"]:.0f}')
        print(f'Jours>lim: {best["jours_limit"]}')
        print()
        print('WR par signal :')
        for sig, wr in best['by_signal'].items():
            print(f'  {sig:<25} : {wr}% WR')
        print()
        print('WR par day type :')
        for dt, wr in best['by_daytype'].items():
            print(f'  {dt:<25} : {wr}% WR')
        print()

        # VERDICT
        if best['pf'] >= 1.3 and best['max_dd'] < 3000:
            print('VERDICT: PF > 1.3 ET DD < $3,000')
        elif best['pf'] >= 1.0:
            print('VERDICT: PF > 1.0 MAIS A AFFINER')
        else:
            print('VERDICT: PROBLEME STRUCTUREL PERSISTANT')

        Path('data').mkdir(exist_ok=True)
        Path('data/dalton_optimization.json').write_text(json.dumps(results[:10], default=str))
        print('\nSauvegarde -> data/dalton_optimization.json')

    await client.logout()
    print('\nTermine.')


asyncio.run(optimize())
