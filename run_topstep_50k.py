"""Topstep $50k Trading Combine — 2 MNQ — SL dynamique sans SAR."""
import asyncio
import sys
import os

sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

DAILY_LIMIT = -1000
ACCOUNT_MAX_LOSS = -2000
PROFIT_TARGET = 3000
POINT_VALUE = 2.0
CONTRACTS = 2
DOLLAR_PER_PT = POINT_VALUE * CONTRACTS  # $4/pt


async def run():
    from projectx_api import ProjectXClient, ConnectionURLS, AggregationUnit
    from backtester.opr_engine import OPREngine
    import pandas as pd
    import numpy as np
    from datetime import datetime, timedelta, date as dt_date, timedelta as dt_td

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

    params = {
        'tp_long': 217.75,
        'tp_short': 205.75,
        'sl_type': 'periods_high_low',
        'sl_long_periods': 9,
        'sl_long_delta': -41.75,
        'sl_short_periods': 15,
        'sl_short_delta': 0.25,
        'sl_max_pts': 200,
        'max_trades': 6,
        'max_longs': 3,
        'max_shorts': 3,
        'min_range': 15,
        'max_range': 999,
        'close_hour': 20,
        'close_min': 49,
        'point_value': POINT_VALUE,
        'contracts': CONTRACTS,
        'daily_loss_limit': DAILY_LIMIT,
        'sar_enabled': False,
    }

    engine = OPREngine(params=params)
    r = engine.run(df5, daily_loss_limit=DAILY_LIMIT, max_trades_per_day=6)

    if not r:
        print('Aucun trade')
        await client.logout()
        return

    trades = r.trades
    pnl_vals = list(r.daily_pnl.values())

    # Group trades by day
    trades_by_day = {}
    for t in trades:
        day = t['entry_time'][:10]
        if day not in trades_by_day:
            trades_by_day[day] = []
        trades_by_day[day].append(t)

    # SL stats
    sl_pts_list = [t['sl_pts'] for t in trades]
    sl_capped_count = sum(1 for s in sl_pts_list if s >= 200)
    sl_uncapped_count = len(sl_pts_list) - sl_capped_count
    sl_avg = np.mean(sl_pts_list) if sl_pts_list else 0
    sl_median = np.median(sl_pts_list) if sl_pts_list else 0

    # Best / worst day
    best_day_key = max(r.daily_pnl, key=r.daily_pnl.get)
    best_pnl = r.daily_pnl[best_day_key]
    worst_day_key = min(r.daily_pnl, key=r.daily_pnl.get)
    worst_pnl = r.daily_pnl[worst_day_key]

    # Days over limits
    j_daily = sum(1 for p in pnl_vals if p < DAILY_LIMIT)
    j_max_loss = sum(1 for p in pnl_vals if p < ACCOUNT_MAX_LOSS)

    # Equity curve + first day >= profit target
    all_trade_days = sorted(r.daily_pnl.keys())
    equity_curve = {}
    eq = 0
    combine_day = None
    combine_count = None
    for i, day in enumerate(all_trade_days):
        eq += r.daily_pnl[day]
        equity_curve[day] = eq
        if eq >= PROFIT_TARGET and combine_day is None:
            combine_day = day
            combine_count = i + 1

    # ======================== HEADER ========================
    print('=' * 80)
    print(f'TOPSTEP $50K TRADING COMBINE -- {CONTRACTS} MNQ (${DOLLAR_PER_PT:.0f}/pt)')
    print(f'OPR + SL Dynamique (PeriodsHighLow cap 200) — sans SAR')
    print(f'TP Long: {params["tp_long"]}  TP Short: {params["tp_short"]}')
    print(f'Close: {params["close_hour"]}h{params["close_min"]:02d}')
    print(f'Limites: Daily -${abs(DAILY_LIMIT):,}  Max Loss -${abs(ACCOUNT_MAX_LOSS):,}  Target +${PROFIT_TARGET:,}')
    print('=' * 80)
    print()

    # ======================== STATS ========================
    print(f'  Trades       : {r.total_trades} (L:{r.long_trades} S:{r.short_trades})')
    print(f'  Win Rate     : {r.win_rate}%')
    print(f'  PnL Total    : ${r.total_pnl_dollars:+,.0f}')
    print(f'  Profit Factor: {r.profit_factor}')
    print(f'  Sharpe Ratio : {r.sharpe_ratio}')
    print(f'  Max Drawdown : ${r.max_drawdown:,.0f}')
    print(f'  Best Day     : {best_day_key} (${best_pnl:+,.0f})')
    print(f'  Worst Day    : {worst_day_key} (${worst_pnl:+,.0f})')
    print(f'  Proj/mois    : ${np.mean(pnl_vals) * 20:+,.0f}')
    print()
    print(f'  Jours > -${abs(DAILY_LIMIT):,}  : {j_daily}')
    print(f'  Jours > -${abs(ACCOUNT_MAX_LOSS):,}  : {j_max_loss}')
    print(f'  Jours trades : {len(pnl_vals)} (profit:{sum(1 for p in pnl_vals if p > 0)} perte:{sum(1 for p in pnl_vals if p < 0)})')
    print()
    print(f'  SL moyen     : {sl_avg:.1f} pts')
    print(f'  SL median    : {sl_median:.1f} pts')
    print(f'  SL plafonne (200pts) : {sl_capped_count} fois')
    print(f'  SL dynamique (<200)  : {sl_uncapped_count} fois')
    print()

    if combine_day:
        print(f'  Equity >= ${PROFIT_TARGET:,} le : {combine_day}')
        print(f'  Nombre de jours pour valider le Combine : {combine_count} jours')
    else:
        print(f'  Equity n\'atteint JAMAIS ${PROFIT_TARGET:,} (max: ${max(equity_curve.values()):+,.0f})')
    print()

    # ======================== P&L JOUR PAR JOUR ========================
    print('=' * 80)
    print('P&L JOUR PAR JOUR')
    print('=' * 80)
    print(f'{"Date":12s} {"Jour":4s}  {"P&L":>9s}  {"Equity":>10s}  {"Nb":>2s}  {"W":>2s}  {"L":>2s}  {"":30s}')
    print('-' * 80)

    weekday_n = {0: 'LUN', 1: 'MAR', 2: 'MER', 3: 'JEU', 4: 'VEN'}
    first_c = dt_date.fromisoformat(all_trade_days[0])
    last_c = dt_date.fromisoformat(all_trade_days[-1])
    eq_c = 0
    prev_month_c = ''

    dc = first_c
    while dc <= last_c:
        if dc.weekday() >= 5:
            dc += dt_td(days=1)
            continue
        ds = str(dc)
        month_c = ds[:7]
        if month_c != prev_month_c:
            if prev_month_c:
                print()
            prev_month_c = month_c

        pnl_c = r.daily_pnl.get(ds, 0)
        eq_c += pnl_c
        dt_list = trades_by_day.get(ds, [])
        nb = len(dt_list)
        wins = sum(1 for t in dt_list if t['pnl_dollars'] > 0)
        losses = sum(1 for t in dt_list if t['pnl_dollars'] < 0)
        wd = weekday_n.get(dc.weekday(), '???')

        if nb == 0:
            bar = '  (pas de trade)'
        else:
            bar_len = min(int(abs(pnl_c) / 50), 30)
            bar = ('#' if pnl_c >= 0 else '-') * bar_len

        flag = ''
        if pnl_c < ACCOUNT_MAX_LOSS:
            flag = ' !! MAX LOSS'
        elif pnl_c < DAILY_LIMIT:
            flag = ' ! DAILY'

        # Mark combine validation day
        combine_flag = ''
        if combine_day and ds == combine_day:
            combine_flag = ' [OK] COMBINE VALIDE!'

        sign = '+' if pnl_c >= 0 else '-'
        print(f'{ds} {wd:4s}  {sign}${abs(pnl_c):>7,.0f}  eq${eq_c:>+9,.0f}  {nb:>2d}  {wins:>2d}  {losses:>2d}  {bar}{flag}{combine_flag}')

        dc += dt_td(days=1)

    print()
    print(f'TOTAL : ${r.total_pnl_dollars:+,.0f}   {r.total_trades} trades   WR {r.win_rate}%   PF {r.profit_factor}   Sharpe {r.sharpe_ratio}')
    print()

    # ======================== WORST DAY ANALYSIS ========================
    worst_trades = trades_by_day.get(worst_day_key, [])
    reason_labels = {
        'tp': 'TP', 'sl': 'SL', 'time_exit': 'TI',
        'daily_limit': 'DL', 'eod': 'EOD',
    }

    print('=' * 80)
    print(f'ANALYSE WORST DAY : {worst_day_key} (${worst_pnl:+,.0f})')
    print('=' * 80)
    print()

    for i, t in enumerate(worst_trades, 1):
        d = t['direction'].upper()
        entry_h = t['entry_time'][11:16]
        exit_h = t['exit_time'][11:16]
        reason = reason_labels.get(t['exit_reason'], t['exit_reason'])
        pnl_sign = '+' if t['pnl_dollars'] >= 0 else '-'

        if t['direction'] == 'long':
            tp_target = t['entry_price'] + params['tp_long']
            sl_target = t['entry_price'] - t['sl_pts']
        else:
            tp_target = t['entry_price'] - params['tp_short']
            sl_target = t['entry_price'] + t['sl_pts']

        print(f'  Trade {i}: {d:5s} {entry_h} > {exit_h}')
        print(f'    Entry    : {t["entry_price"]:.2f}')
        print(f'    Exit     : {t["exit_price"]:.2f}  ({reason})')
        print(f'    PnL      : {pnl_sign}${abs(t["pnl_dollars"]):,.0f} ({t["pnl_points"]:+.1f}pts)')
        print(f'    SL       : {sl_target:.2f} ({t["sl_pts"]:.0f}pts = ${t["sl_pts"] * DOLLAR_PER_PT:.0f})')
        print(f'    Range    : {t["range_low"]:.2f} - {t["range_high"]:.2f} ({t["range_size"]:.0f}pts)')
        print()

    # ======================== VERDICT ========================
    print('=' * 80)
    print('VERDICT')
    print('=' * 80)
    print()

    issues = []
    if j_daily > 0:
        issues.append(f'{j_daily} jour(s) depassent la limite daily de -${abs(DAILY_LIMIT):,}')
    if j_max_loss > 0:
        issues.append(f'{j_max_loss} jour(s) depassent le max loss de -${abs(ACCOUNT_MAX_LOSS):,}')
    if r.max_drawdown > abs(ACCOUNT_MAX_LOSS):
        issues.append(f'Max DD (${r.max_drawdown:,.0f}) > max loss account (${abs(ACCOUNT_MAX_LOSS):,})')

    # Check trailing drawdown (cumulative equity never drops > $2000 from peak)
    eq_peak = 0
    eq_run = 0
    max_trail_dd = 0
    for day in all_trade_days:
        eq_run += r.daily_pnl[day]
        if eq_run > eq_peak:
            eq_peak = eq_run
        trail_dd = eq_peak - eq_run
        if trail_dd > max_trail_dd:
            max_trail_dd = trail_dd
    if max_trail_dd > abs(ACCOUNT_MAX_LOSS):
        issues.append(f'Trailing DD (${max_trail_dd:,.0f}) depasse max loss (${abs(ACCOUNT_MAX_LOSS):,}) -> COMPTE CASSE')

    if combine_day:
        print(f'  [OK] Combine VALIDE en {combine_count} jours (le {combine_day})')
        print(f'     Equity finale: ${eq_c:+,.0f}')
    else:
        print(f'  [FAIL] Combine NON VALIDE (equity max: ${max(equity_curve.values()):+,.0f}, target: ${PROFIT_TARGET:,})')

    if max_trail_dd <= abs(ACCOUNT_MAX_LOSS):
        print(f'  [OK] Trailing DD OK: ${max_trail_dd:,.0f} < ${abs(ACCOUNT_MAX_LOSS):,}')
    else:
        print(f'  [FAIL] Trailing DD DEPASSE: ${max_trail_dd:,.0f} > ${abs(ACCOUNT_MAX_LOSS):,}')

    if j_daily == 0:
        print(f'  [OK] Aucun jour > -${abs(DAILY_LIMIT):,}')
    else:
        print(f'  ! DAILY  {j_daily} jour(s) > -${abs(DAILY_LIMIT):,}')

    print()
    if not issues:
        print('  STRATEGIE VIABLE pour Topstep $50k avec 2 MNQ.')
    else:
        print('  PROBLEMES:')
        for issue in issues:
            print(f'    - {issue}')

    print()
    await client.logout()


asyncio.run(run())
