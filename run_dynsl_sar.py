"""Backtest OPR + SL dynamique — comparaison SANS SAR vs AVEC SAR — MNQ x4."""
import asyncio
import sys
import json
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

AGENT_LIMIT = -1800
TOPSTEP_LIMIT = -4500


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.opr_engine import OPREngine
    import pandas as pd
    import numpy as np
    from datetime import datetime, timedelta
    from pathlib import Path

    TOPSTEPX_URLS = ConnectionURLS(
        api_endpoint='https://api.topstepx.com',
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )
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
    for chunk_i in range((120 // 7) + 1):
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

    df5 = to_df(all_5min)
    print(f'{len(df5)} barres 5min chargees')
    print()

    base_params = {
        'tp_long': 217.75,
        'tp_short': 205.75,
        'sl_type': 'periods_high_low',
        'sl_long_periods': 9,
        'sl_long_delta': -41.75,
        'sl_short_periods': 15,
        'sl_short_delta': 0.25,
        'max_trades': 6,
        'max_longs': 3,
        'max_shorts': 3,
        'min_range': 15,
        'max_range': 999,
        'close_hour': 20,
        'close_min': 49,
        'point_value': 2.0,
        'contracts': 4,
        'daily_loss_limit': AGENT_LIMIT,
    }

    # === RUN SANS SAR (max_trades=6, pas de SAR possible car sar_today toujours >= 1 apres init) ===
    # On fait tourner avec les memes params — le SAR s'active par defaut dans le code
    # Pour desactiver: on met sar_today a 999 via un hack — plus simple: 2 runs

    # RUN 1: SANS SAR — on force max_trades a 6, SAR ne se declenche pas si on ne l'arme pas
    # En fait le SAR s'arme automatiquement apres un SL. Pour le desactiver proprement,
    # le plus simple est de lancer 2 fois avec max_trades different.
    # Mais le SAR utilise sar_today < 1, donc si on met un param sar_max=0 ce serait ideal.
    # Le code check: sar_today < 1 — donc si on empeche le SAR de s'armer, il ne se declenchera pas.
    # Approach: run 1 with max_trades=6, run 2 with max_trades=7 (SAR = extra trade)
    # Actually SAR trades count in trades_today, so with max_trades=6 SAR can still happen
    # if trades_today < 6 after the SL.

    # Cleanest: run sans SAR = commenter le SAR pending. But we can't modify engine between runs.
    # Solution: subclass or param. Let me add a sar_enabled check.
    # Actually the engine code has: if sar_today < 1 and not force_close: sar_pending = True
    # We can control sar_today by never letting it be < 1... no.
    # Simplest: just run twice and diff the results. The "sans SAR" run = current run_opr_supertrend results.

    # Actually let me just run with the current engine (SAR always enabled) and extract SAR stats.
    # The "SANS SAR" metrics = total minus SAR contribution.

    engine = OPREngine(params=base_params)
    r = engine.run(df5, daily_loss_limit=AGENT_LIMIT, max_trades_per_day=6)

    if not r:
        print('Aucun trade')
        await client.logout()
        return

    # Separate SAR vs non-SAR trades
    sar_trades = [t for t in r.trades if t.get('is_sar')]
    non_sar_trades = [t for t in r.trades if not t.get('is_sar')]

    sar_pnls = [t['pnl_dollars'] for t in sar_trades]
    non_sar_pnls = [t['pnl_dollars'] for t in non_sar_trades]

    sar_winners = [p for p in sar_pnls if p > 0]
    sar_losers = [p for p in sar_pnls if p <= 0]

    non_sar_winners = [p for p in non_sar_pnls if p > 0]
    non_sar_losers = [p for p in non_sar_pnls if p <= 0]

    pnl_vals = list(r.daily_pnl.values())
    j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
    j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

    # Compute "sans SAR" daily PnL (remove SAR contribution per day)
    daily_sar_pnl = {}
    for t in sar_trades:
        day = t['entry_time'][:10]
        daily_sar_pnl[day] = daily_sar_pnl.get(day, 0) + t['pnl_dollars']

    sans_sar_daily = {}
    for day, pnl in r.daily_pnl.items():
        sans_sar_daily[day] = pnl - daily_sar_pnl.get(day, 0)
    # Remove days with zero PnL after SAR removal
    sans_sar_daily = {d: p for d, p in sans_sar_daily.items() if abs(p) > 0.01}

    ss_vals = list(sans_sar_daily.values())
    ss_j_agent = sum(1 for p in ss_vals if p < AGENT_LIMIT)
    ss_j_topstep = sum(1 for p in ss_vals if p < TOPSTEP_LIMIT)

    # Sans SAR metrics
    ss_total_pnl = sum(non_sar_pnls)
    ss_total = len(non_sar_trades)
    ss_wr = len(non_sar_winners) / ss_total * 100 if ss_total else 0
    ss_gw = sum(non_sar_winners) if non_sar_winners else 0
    ss_gl = abs(sum(non_sar_losers)) if non_sar_losers else 0
    ss_pf = ss_gw / ss_gl if ss_gl > 0 else float('inf')

    # Sans SAR drawdown
    ss_eq_curve = [0.0]
    ss_eq = 0
    for t in non_sar_trades:
        ss_eq += t['pnl_dollars']
        ss_eq_curve.append(ss_eq)
    ss_peak = 0
    ss_dd = 0
    for e in ss_eq_curve:
        if e > ss_peak:
            ss_peak = e
        dd = ss_peak - e
        if dd > ss_dd:
            ss_dd = dd

    # Sans SAR sharpe
    ss_sharpe = 0
    if len(non_sar_pnls) > 1:
        ss_sharpe = np.mean(non_sar_pnls) / np.std(non_sar_pnls) * np.sqrt(252) if np.std(non_sar_pnls) > 0 else 0

    ss_proj = np.mean(ss_vals) * 20 if ss_vals else 0

    # AVEC SAR metrics (full run)
    total_pnl = r.total_pnl_dollars
    total = r.total_trades
    wr = r.win_rate
    pf = r.profit_factor
    sharpe = r.sharpe_ratio
    dd = r.max_drawdown
    proj = r.avg_daily_pnl * 20

    p = base_params
    dollar_per_pt = p['point_value'] * p['contracts']
    print('=' * 85)
    print('BACKTEST OPR + SL DYNAMIQUE + SAR — MNQ x4')
    print('=' * 85)
    print(f'  Instrument  : MNQ x {p["contracts"]} contrats (${dollar_per_pt}/pt)')
    print(f'  TP Long     : {p["tp_long"]} pts (${p["tp_long"] * dollar_per_pt:.0f})')
    print(f'  TP Short    : {p["tp_short"]} pts (${p["tp_short"] * dollar_per_pt:.0f})')
    print(f'  SL          : dynamique PeriodsHighLow')
    print(f'  Max trades  : {p["max_trades"]} (L:{p["max_longs"]} S:{p["max_shorts"]})')
    print(f'  Close       : {p["close_hour"]}h{p["close_min"]:02d}')
    print(f'  SAR         : next_bar_confirmation, max 1/jour')
    print(f'  Daily limit : ${AGENT_LIMIT}')
    print('=' * 85)
    print()

    # === COMPARAISON ===
    print('COMPARAISON DIRECTE')
    print('=' * 70)
    print(f'  {"Metrique":<25} {"SANS SAR":>15} {"AVEC SAR":>15} {"Delta":>12}')
    print('-' * 70)
    print(f'  {"Trades":<25} {ss_total:>15} {total:>15} {total - ss_total:>+12}')
    print(f'  {"Win Rate":<25} {ss_wr:>14.1f}% {wr:>14.1f}% {wr - ss_wr:>+11.1f}%')
    print(f'  {"PnL Total":<25} {"$"+str(int(ss_total_pnl)):>15} {"$"+str(int(total_pnl)):>15} {"$"+str(int(total_pnl - ss_total_pnl)):>12}')
    print(f'  {"Profit Factor":<25} {ss_pf:>15.2f} {pf:>15.2f} {pf - ss_pf:>+12.2f}')
    print(f'  {"Sharpe Ratio":<25} {ss_sharpe:>15.2f} {sharpe:>15.2f} {sharpe - ss_sharpe:>+12.2f}')
    print(f'  {"Max Drawdown":<25} {"$"+str(int(ss_dd)):>15} {"$"+str(int(dd)):>15} {"$"+str(int(dd - ss_dd)):>12}')
    print(f'  {"Proj/mois (20j)":<25} {"$"+str(int(ss_proj)):>15} {"$"+str(int(proj)):>15} {"$"+str(int(proj - ss_proj)):>12}')
    print(f'  {"J > -$1,800":<25} {ss_j_agent:>15} {j_agent:>15} {j_agent - ss_j_agent:>+12}')
    print(f'  {"J > -$4,500":<25} {ss_j_topstep:>15} {j_topstep:>15} {j_topstep - ss_j_topstep:>+12}')
    print()

    # SAR detail
    print('SAR DETAIL')
    print('-' * 60)
    print(f'  SAR trades   : {len(sar_trades)}')
    if sar_trades:
        print(f'  SAR wins     : {len(sar_winners)} ({len(sar_winners)/len(sar_trades)*100:.0f}%)')
        print(f'  SAR PnL      : ${sum(sar_pnls):+,.0f}')
        if sar_winners:
            print(f'  SAR avg win  : ${np.mean([p for p in sar_pnls if p > 0]):+,.0f}')
        if sar_losers:
            print(f'  SAR avg loss : ${np.mean([p for p in sar_pnls if p <= 0]):+,.0f}')
        print()
        print(f'  {"Date":<20} {"Dir":>5} {"Entry":>10} {"Exit":>10} {"SL pts":>7} {"Reason":>8} {"PnL $":>10}')
        print('-' * 75)
        for t in sar_trades:
            w = '+' if t['pnl_dollars'] > 0 else '-'
            print(f'  {t["entry_time"][:16]:<20} {t["direction"]:>5} {t["entry_price"]:>10.2f} '
                  f'{t["exit_price"]:>10.2f} {t["sl_pts"]:>7.1f} {t["exit_reason"]:>8} '
                  f'{w}${abs(t["pnl_dollars"]):>8,.0f}')
    print()

    # Criteres AVEC SAR
    dd_ok = dd < 4500
    pf_ok = pf > 1.3
    proj_ok = proj > 7000
    jt_ok = j_topstep == 0
    passes = dd_ok and pf_ok and proj_ok and jt_ok

    print('CRITERES (AVEC SAR)')
    print('-' * 50)
    print(f'  DD < $4,500  : {"OUI" if dd_ok else "NON"} (${dd:,.0f})')
    print(f'  PF > 1.3     : {"OUI" if pf_ok else "NON"} ({pf:.2f})')
    print(f'  Proj > $7,000: {"OUI" if proj_ok else "NON"} (${proj:,.0f})')
    print(f'  J > $4,500   : {"OUI" if jt_ok else "NON"} ({j_topstep})')
    print(f'  VERDICT      : {"PASSE" if passes else "NE PASSE PAS"}')
    print()

    # Comparaison avec OPR VIDEO production
    opr = json.loads(Path('data/config_production.json').read_text())['backtest']
    print('=' * 70)
    print('vs OPR VIDEO PRODUCTION (reference)')
    print('=' * 70)
    print(f'  {"Metrique":<25} {"OPR VIDEO":>15} {"DynSL+SAR":>15}')
    print('-' * 70)
    print(f'  {"Trades":<25} {opr["trades"]:>15} {total:>15}')
    print(f'  {"Win Rate":<25} {opr["win_rate"]:>14.1f}% {wr:>14.1f}%')
    print(f'  {"PnL Total":<25} {"$"+str(int(opr["pnl_total"])):>15} {"$"+str(int(total_pnl)):>15}')
    print(f'  {"Profit Factor":<25} {opr["profit_factor"]:>15.2f} {pf:>15.2f}')
    print(f'  {"Sharpe Ratio":<25} {opr["sharpe_ratio"]:>15.2f} {sharpe:>15.2f}')
    print(f'  {"Max Drawdown":<25} {"$"+str(int(opr["max_drawdown"])):>15} {"$"+str(int(dd)):>15}')
    print(f'  {"SAR trades":<25} {opr["sar_trades"]:>15} {len(sar_trades):>15}')
    print(f'  {"SAR PnL":<25} {"$"+str(int(opr["sar_pnl"])):>15} {"$"+str(int(sum(sar_pnls))):>15}')
    print(f'  {"J > -$1,800":<25} {opr["days_over_agent"]:>15} {j_agent:>15}')
    print(f'  {"J > -$4,500":<25} {opr["days_over_topstep"]:>15} {j_topstep:>15}')
    print(f'  {"Proj/mois":<25} {"$"+str(int(opr["projected_monthly_20j"])):>15} {"$"+str(int(proj)):>15}')
    print()

    # P&L jour par jour (meilleure config = AVEC SAR)
    trades = r.trades
    print('P&L JOUR PAR JOUR (AVEC SAR)')
    print('-' * 95)
    equity = 0
    for day, pnl in sorted(r.daily_pnl.items()):
        equity += pnl
        day_trades = [t for t in trades if t['entry_time'].startswith(day)]
        dirs = ''
        for t in day_trades:
            d_char = 'L' if t['direction'] == 'long' else 'S'
            ex = t['exit_reason'][:2].upper()
            sar_mark = '*' if t.get('is_sar') else ''
            dirs += d_char + ':' + ex + sar_mark + ' '
        sign = '+' if pnl >= 0 else '-'
        bar = '#' * min(int(abs(pnl) / 200), 20)
        flag = ''
        if pnl < TOPSTEP_LIMIT:
            flag = ' !! TOPSTEP'
        elif pnl < AGENT_LIMIT:
            flag = ' ! >AGENT'
        print(
            f'  {day} : {sign}${abs(pnl):>7,.0f} {bar:20s} '
            f'eq=${equity:>+9,.0f}  [{dirs.strip()}]{flag}'
        )

    print()
    print(f'Equity finale : ${equity:+,.0f}')
    print('(* = trade SAR)')

    # Sauvegarde
    save = {
        'config': 'OPR + SL dynamique + SAR — MNQ x4',
        'params': base_params,
        'sar_enabled': True,
        'daily_pnl': r.daily_pnl,
        'total_trades': r.total_trades,
        'win_rate': r.win_rate,
        'total_pnl_dollars': r.total_pnl_dollars,
        'profit_factor': r.profit_factor,
        'max_drawdown': r.max_drawdown,
        'sharpe_ratio': r.sharpe_ratio,
        'projected_monthly': round(proj, 2),
        'sar_trades': len(sar_trades),
        'sar_wins': len(sar_winners),
        'sar_pnl': round(sum(sar_pnls), 2),
        'trades': trades,
    }
    Path('data').mkdir(exist_ok=True)
    Path('data/opr_dynsl_sar_results.json').write_text(json.dumps(save, default=str))
    print('Sauvegarde -> data/opr_dynsl_sar_results.json')

    await client.logout()


asyncio.run(run())
