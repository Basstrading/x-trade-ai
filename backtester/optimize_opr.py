"""Optimise les paramètres OPR — Contrainte DD < $1,800."""
import asyncio
import sys
import os
import json
import itertools
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv()

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

# Grille réduite : 3×3×3×3×3×1 = 243 combos
PARAM_GRID = {
    'tp_long':    [40, 50, 60],
    'tp_short':   [30, 40, 50],
    'sl_long':    [15, 25, 35],
    'sl_short':   [12, 20, 30],
    'min_range':  [15, 25, 40],
    'max_range':  [150],
    'max_trades': [2, 3, 4],
}


async def optimize():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.opr_engine import OPREngine
    import pandas as pd
    import numpy as np
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
    all_5min = []
    n_chunks = (DAYS // 7) + 1

    print(f'Fetching {DAYS}j 5min data...')
    for chunk_i in range(n_chunks):
        chunk_end = now - timedelta(days=chunk_i * 7)
        chunk_start = chunk_end - timedelta(days=7)
        try:
            bars5 = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=10000, includePartialBar=False,
            )
            if bars5:
                all_5min.extend(bars5)
        except Exception:
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

    df5 = to_df(all_5min)
    print(f'5min: {len(df5)} barres')

    # ================================================================
    # PHASE 1 : GRID SEARCH — DD < $1,800
    # ================================================================
    keys = list(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combos = list(itertools.product(*values))
    print(f'\n=== PHASE 1 : GRID SEARCH ({len(combos)} combos) ===\n')

    results = []
    all_tested = 0
    t0 = time.time()

    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        all_tested += 1

        engine = OPREngine(params=params)
        r = engine.run(df5, daily_loss_limit=-900, max_trades_per_day=params['max_trades'])

        if not r or r.total_trades < 20:
            continue
        if r.days_over_topstep_limit > 0:
            continue
        if r.max_drawdown > 1800:
            continue

        pf = min(r.profit_factor, 10.0)
        score = (
            pf * 0.30
            + (r.win_rate / 100) * 0.20
            + min(r.sharpe_ratio, 3) / 3 * 0.20
            + (r.projected_monthly / 10000) * 0.15
            - (max(0, r.max_drawdown - 1800) / 1000) * 0.15
        )
        results.append({
            'params': params,
            'trades': r.total_trades,
            'win_rate': r.win_rate,
            'pnl': r.total_pnl_dollars,
            'pf': round(pf, 2),
            'sharpe': r.sharpe_ratio,
            'max_dd': r.max_drawdown,
            'long_trades': r.long_trades,
            'long_wr': r.long_winrate,
            'short_trades': r.short_trades,
            'short_wr': r.short_winrate,
            'exits_tp': r.exits_tp,
            'exits_sl': r.exits_sl,
            'exits_time': r.exits_time,
            'jours_limit': r.days_over_agent_limit,
            'jours_topstep': r.days_over_topstep_limit,
            'proj_month': r.projected_monthly,
            'worst_day': r.worst_day,
            'score': round(score, 4),
        })

        if (idx + 1) % 1000 == 0 or idx == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            eta = (len(combos) - idx - 1) / rate if rate > 0 else 0
            print(f'  {idx+1}/{len(combos)} ({rate:.1f}/s, ETA {eta:.0f}s) DD<1800={len(results)}')

    results.sort(key=lambda x: x['score'], reverse=True)

    elapsed = time.time() - t0
    print(f'\nPhase 1 termine en {elapsed:.0f}s')
    print(f'Combos testees  : {all_tested}')
    print(f'Combos DD<$1800 : {len(results)}')

    # Affichage TOP 5
    print()
    print('=' * 110)
    print('TOP 5 (DD < $1,800)')
    print('=' * 110)
    print(
        f'{"#":>2} {"PF":>5} {"WR":>6} {"PnL":>9} {"DD":>7} {"J>lim":>6} '
        f'{"Proj":>8} {"tp_l":>5} {"sl_l":>5} {"tp_s":>5} {"sl_s":>5} '
        f'{"rng":>10} {"maxT":>4} {"Score":>6}'
    )
    print('-' * 110)

    for i, r in enumerate(results[:5]):
        p = r['params']
        print(
            f'{i+1:>2} {r["pf"]:>5.2f} {r["win_rate"]:>5.1f}% '
            f'${r["pnl"]:>8.0f} ${r["max_dd"]:>6.0f} {r["jours_limit"]:>6} '
            f'${r["proj_month"]:>7.0f} '
            f'{p["tp_long"]:>5} {p["sl_long"]:>5} {p["tp_short"]:>5} {p["sl_short"]:>5} '
            f'{p["min_range"]:>4}-{p["max_range"]:<5} '
            f'{p["max_trades"]:>4} '
            f'{r["score"]:>6.3f}'
        )

    # Aussi afficher TOP 5 par PnL brut
    by_pnl = sorted(results, key=lambda x: x['pnl'], reverse=True)
    print()
    print('TOP 5 par P&L brut (DD < $1,800) :')
    for i, r in enumerate(by_pnl[:5]):
        p = r['params']
        print(
            f'  {i+1}. PnL=${r["pnl"]:+.0f} PF={r["pf"]} WR={r["win_rate"]}% '
            f'DD=${r["max_dd"]:.0f} Proj=${r["proj_month"]:.0f}/m '
            f'tp={p["tp_long"]}/{p["tp_short"]} sl={p["sl_long"]}/{p["sl_short"]} '
            f'rng={p["min_range"]}-{p["max_range"]} maxT={p["max_trades"]}'
        )

    # ================================================================
    # PHASE 2A : VARIANTE LONGS SEULEMENT
    # ================================================================
    print()
    print('=' * 110)
    print('PHASE 2A : LONGS SEULEMENT (max_shorts=0)')
    print('=' * 110)

    # Prendre les meilleurs TP/SL long du grid et tester long-only
    # 3×3×3×1×3 = 81 combos
    long_grid = {
        'tp_long':    [40, 50, 60],
        'sl_long':    [15, 25, 35],
        'min_range':  [15, 25, 40],
        'max_range':  [150],
        'max_trades': [2, 3, 4],
    }
    lk = list(long_grid.keys())
    lv = [long_grid[k] for k in lk]
    long_combos = list(itertools.product(*lv))

    long_results = []
    for combo in long_combos:
        params = dict(zip(lk, combo))
        params['max_shorts'] = 0
        params['tp_short'] = 50
        params['sl_short'] = 30

        engine = OPREngine(params=params)
        r = engine.run(df5, daily_loss_limit=-900, max_trades_per_day=params['max_trades'])

        if not r or r.total_trades < 20:
            continue
        if r.days_over_topstep_limit > 0:
            continue
        if r.max_drawdown > 1800:
            continue

        pf = min(r.profit_factor, 10.0)
        score = (
            pf * 0.30
            + (r.win_rate / 100) * 0.20
            + min(r.sharpe_ratio, 3) / 3 * 0.20
            + (r.projected_monthly / 10000) * 0.15
            - (max(0, r.max_drawdown - 1800) / 1000) * 0.15
        )
        long_results.append({
            'params': params,
            'trades': r.total_trades,
            'win_rate': r.win_rate,
            'pnl': r.total_pnl_dollars,
            'pf': round(pf, 2),
            'sharpe': r.sharpe_ratio,
            'max_dd': r.max_drawdown,
            'long_trades': r.long_trades,
            'long_wr': r.long_winrate,
            'short_trades': r.short_trades,
            'short_wr': r.short_winrate,
            'exits_tp': r.exits_tp,
            'exits_sl': r.exits_sl,
            'exits_time': r.exits_time,
            'jours_limit': r.days_over_agent_limit,
            'proj_month': r.projected_monthly,
            'worst_day': r.worst_day,
            'score': round(score, 4),
        })

    long_results.sort(key=lambda x: x['score'], reverse=True)
    print(f'Combos long-only DD<$1800 : {len(long_results)}')

    if long_results:
        print()
        print('TOP 3 LONGS SEULEMENT :')
        for i, r in enumerate(long_results[:3]):
            p = r['params']
            print(
                f'  {i+1}. PnL=${r["pnl"]:+.0f} PF={r["pf"]} WR={r["win_rate"]}% '
                f'DD=${r["max_dd"]:.0f} Proj=${r["proj_month"]:.0f}/m '
                f'tp_l={p["tp_long"]} sl_l={p["sl_long"]} '
                f'rng={p["min_range"]}-{p["max_range"]} maxT={p["max_trades"]} '
                f'worst=${r["worst_day"]:.0f} J>lim={r["jours_limit"]}'
            )

    # ================================================================
    # PHASE 2B : VARIANTE SHORT BUFFER
    # ================================================================
    print()
    print('=' * 110)
    print('PHASE 2B : SHORT AVEC BUFFER (short_buffer=0,5,10,15)')
    print('=' * 110)

    # Prendre les meilleurs params du grid et tester avec buffer
    buf_results = []
    if results:
        # Tester les top 10 configs avec différents buffers
        top_configs = results[:10]
        for r_cfg in top_configs:
            base_params = r_cfg['params'].copy()
            for buf in [0, 5, 10, 15]:
                params = base_params.copy()
                params['short_buffer'] = buf

                engine = OPREngine(params=params)
                r = engine.run(df5, daily_loss_limit=-900, max_trades_per_day=params['max_trades'])

                if not r or r.total_trades < 20:
                    continue
                if r.days_over_topstep_limit > 0:
                    continue
                if r.max_drawdown > 1800:
                    continue

                pf = min(r.profit_factor, 10.0)
                score = (
                    pf * 0.30
                    + (r.win_rate / 100) * 0.20
                    + min(r.sharpe_ratio, 3) / 3 * 0.20
                    + (r.projected_monthly / 10000) * 0.15
                    - (max(0, r.max_drawdown - 1800) / 1000) * 0.15
                )
                buf_results.append({
                    'params': params,
                    'trades': r.total_trades,
                    'win_rate': r.win_rate,
                    'pnl': r.total_pnl_dollars,
                    'pf': round(pf, 2),
                    'sharpe': r.sharpe_ratio,
                    'max_dd': r.max_drawdown,
                    'long_trades': r.long_trades,
                    'long_wr': r.long_winrate,
                    'short_trades': r.short_trades,
                    'short_wr': r.short_winrate,
                    'jours_limit': r.days_over_agent_limit,
                    'proj_month': r.projected_monthly,
                    'worst_day': r.worst_day,
                    'short_buffer': buf,
                    'score': round(score, 4),
                })

    buf_results.sort(key=lambda x: x['score'], reverse=True)
    print(f'Combos buffer DD<$1800 : {len(buf_results)}')

    if buf_results:
        print()
        print('TOP 3 AVEC SHORT BUFFER :')
        for i, r in enumerate(buf_results[:3]):
            p = r['params']
            print(
                f'  {i+1}. buf={r["short_buffer"]}pts PnL=${r["pnl"]:+.0f} PF={r["pf"]} '
                f'WR={r["win_rate"]}% DD=${r["max_dd"]:.0f} Proj=${r["proj_month"]:.0f}/m '
                f'L={r["long_trades"]}({r["long_wr"]}%) S={r["short_trades"]}({r["short_wr"]}%) '
                f'worst=${r["worst_day"]:.0f}'
            )

    # ================================================================
    # PHASE 3 : RAPPORT COMPARATIF
    # ================================================================
    print()
    print('=' * 110)
    print('RAPPORT COMPARATIF')
    print('=' * 110)

    # Collecter les meilleurs de chaque variante
    variants = []
    if results:
        b = results[0]
        variants.append(('GRID (L+S)', b))
    if long_results:
        b = long_results[0]
        variants.append(('LONGS ONLY', b))
    if buf_results:
        b = buf_results[0]
        variants.append(('SHORT BUFFER', b))

    if variants:
        print(
            f'{"Variante":<16} {"PF":>5} {"WR":>6} {"PnL":>9} {"DD":>7} '
            f'{"Proj/m":>8} {"Worst":>7} {"J>lim":>6} {"Trades":>7} {"Score":>6}'
        )
        print('-' * 90)
        for name, r in variants:
            print(
                f'{name:<16} {r["pf"]:>5.2f} {r["win_rate"]:>5.1f}% '
                f'${r["pnl"]:>8.0f} ${r["max_dd"]:>6.0f} '
                f'${r["proj_month"]:>7.0f} ${r["worst_day"]:>6.0f} '
                f'{r["jours_limit"]:>6} {r["trades"]:>7} {r["score"]:>6.3f}'
            )

    # Trouver la meilleure variante globale
    all_variants = []
    for r in results:
        r['variant'] = 'grid'
    for r in long_results:
        r['variant'] = 'long_only'
    for r in buf_results:
        r['variant'] = 'short_buffer'
    all_variants = results + long_results + buf_results
    all_variants.sort(key=lambda x: x['score'], reverse=True)

    if all_variants:
        best = all_variants[0]
        bp = best['params']
        print()
        print('=' * 110)
        print('MEILLEURE VARIANTE GLOBALE')
        print('=' * 110)
        print(f'Type       : {best["variant"]}')
        print(f'TP Long    : {bp["tp_long"]}pts (${bp["tp_long"]*20})')
        print(f'SL Long    : {bp["sl_long"]}pts (${bp["sl_long"]*20})')
        print(f'TP Short   : {bp.get("tp_short", "N/A")}pts')
        print(f'SL Short   : {bp.get("sl_short", "N/A")}pts')
        if 'short_buffer' in bp:
            print(f'Short Buf  : {bp["short_buffer"]}pts')
        print(f'Min Range  : {bp["min_range"]}pts')
        print(f'Max Range  : {bp["max_range"]}pts')
        print(f'Max Trades : {bp["max_trades"]}')
        print()
        print(f'PF         : {best["pf"]}')
        print(f'Win Rate   : {best["win_rate"]}%')
        print(f'P&L 120j   : ${best["pnl"]:.0f}')
        print(f'Max DD     : ${best["max_dd"]:.0f}')
        print(f'Proj/mois  : ${best["proj_month"]:.0f}')
        print(f'Pire jour  : ${best["worst_day"]:.0f}')
        print(f'Jours>-900 : {best["jours_limit"]}')
        print(f'Long       : {best["long_trades"]} trades / {best["long_wr"]}% WR')
        print(f'Short      : {best["short_trades"]} trades / {best["short_wr"]}% WR')
        print()

        # VERDICT
        if best['pf'] >= 1.5 and best['max_dd'] <= 1800:
            print('VERDICT: EXCELLENT - PF >= 1.5 ET DD <= $1,800')
        elif best['pf'] >= 1.3 and best['max_dd'] <= 1800:
            print('VERDICT: BON - PF >= 1.3 ET DD <= $1,800')
        elif best['pf'] >= 1.0 and best['max_dd'] <= 1800:
            print('VERDICT: CORRECT - PF >= 1.0 ET DD <= $1,800')
        else:
            print('VERDICT: AUCUNE COMBO DD <= $1,800 TROUVEE')

    # Sauvegarde
    save_data = {
        'grid_top20': results[:20],
        'long_only_top10': long_results[:10],
        'short_buffer_top10': buf_results[:10],
        'best_overall': all_variants[0] if all_variants else None,
    }
    Path('data').mkdir(exist_ok=True)
    Path('data/opr_optimization_dd1800.json').write_text(json.dumps(save_data, default=str))
    print('\nSauvegarde -> data/opr_optimization_dd1800.json')

    await client.logout()
    print('\nTermine.')


asyncio.run(optimize())
