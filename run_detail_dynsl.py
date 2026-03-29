"""Detail P&L jour par jour — SL dynamique sans SAR — MNQ x4."""
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
        'point_value': 2.0,
        'contracts': 4,
        'daily_loss_limit': AGENT_LIMIT,
        'sar_enabled': False,
    }

    engine = OPREngine(params=params)
    r = engine.run(df5, daily_loss_limit=AGENT_LIMIT, max_trades_per_day=6)

    if not r:
        print('Aucun trade')
        await client.logout()
        return

    trades = r.trades
    pnl_vals = list(r.daily_pnl.values())
    j_agent = sum(1 for p in pnl_vals if p < AGENT_LIMIT)
    j_topstep = sum(1 for p in pnl_vals if p < TOPSTEP_LIMIT)

    print('=' * 80)
    print('OPR + SL DYNAMIQUE (sans SAR) -- MNQ x4 ($8/pt)')
    print(f'TP Long: {params["tp_long"]}  TP Short: {params["tp_short"]}')
    print(f'SL: PeriodsHighLow  Close: {params["close_hour"]}h{params["close_min"]:02d}')
    print('=' * 80)
    print()

    # Group trades by day
    trades_by_day = {}
    for t in trades:
        day = t['entry_time'][:10]
        if day not in trades_by_day:
            trades_by_day[day] = []
        trades_by_day[day].append(t)

    # ===================== TABLEAU COMPACT P&L PAR JOUR =====================
    print('=' * 80)
    print('P&L JOUR PAR JOUR (compact)')
    print('=' * 80)
    print(f'{"Date":12s} {"Jour":4s}  {"P&L":>9s}  {"Equity":>10s}  {"Nb":>2s}  {"W":>2s}  {"L":>2s}  {"":30s}')
    print('-' * 80)

    from datetime import date as dt_date_c, timedelta as dt_td_c
    all_trade_days_c = sorted(r.daily_pnl.keys())
    first_c = dt_date_c.fromisoformat(all_trade_days_c[0])
    last_c = dt_date_c.fromisoformat(all_trade_days_c[-1])
    weekday_n = {0: 'LUN', 1: 'MAR', 2: 'MER', 3: 'JEU', 4: 'VEN'}
    eq_c = 0
    prev_month_c = ''

    dc = first_c
    while dc <= last_c:
        if dc.weekday() >= 5:
            dc += dt_td_c(days=1)
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
            bar_len = min(int(abs(pnl_c) / 100), 30)
            bar = ('#' if pnl_c >= 0 else '-') * bar_len

        flag = ''
        if pnl_c < TOPSTEP_LIMIT:
            flag = ' !!TOPSTEP'
        elif pnl_c < AGENT_LIMIT:
            flag = ' !AGENT'

        sign = '+' if pnl_c >= 0 else '-'
        print(f'{ds} {wd:4s}  {sign}${abs(pnl_c):>7,.0f}  eq${eq_c:>+9,.0f}  {nb:>2d}  {wins:>2d}  {losses:>2d}  {bar}{flag}')

        dc += dt_td_c(days=1)

    print()
    print(f'TOTAL : +${r.total_pnl_dollars:>8,.0f}   {r.total_trades} trades   WR {r.win_rate}%   PF {r.profit_factor}   Sharpe {r.sharpe_ratio}')
    print()
    print()

    # ===================== DETAIL TRADE PAR TRADE =====================
    # Build ALL weekdays in range
    from datetime import date as dt_date, timedelta as dt_timedelta
    all_trade_days = sorted(r.daily_pnl.keys())
    first_day = dt_date.fromisoformat(all_trade_days[0])
    last_day = dt_date.fromisoformat(all_trade_days[-1])

    all_weekdays = []
    d = first_day
    while d <= last_day:
        if d.weekday() < 5:  # lun-ven
            all_weekdays.append(str(d))
        d += dt_timedelta(days=1)

    # Group ALL weekdays by month
    days_by_month = {}
    for day in all_weekdays:
        month = day[:7]
        if month not in days_by_month:
            days_by_month[month] = []
        days_by_month[month].append(day)

    equity = 0
    grand_total = 0

    reason_labels = {
        'tp': 'TP', 'sl': 'SL', 'time_exit': 'TI',
        'daily_limit': 'DL', 'eod': 'EOD',
    }

    for month, days in sorted(days_by_month.items()):
        month_pnl = 0
        month_trades = 0
        month_wins = 0

        # Month header
        month_names = {
            '01': 'JANVIER', '02': 'FEVRIER', '03': 'MARS',
            '04': 'AVRIL', '05': 'MAI', '06': 'JUIN',
            '07': 'JUILLET', '08': 'AOUT', '09': 'SEPTEMBRE',
            '10': 'OCTOBRE', '11': 'NOVEMBRE', '12': 'DECEMBRE',
        }
        mm = month.split('-')[1]
        yyyy = month.split('-')[0]
        print(f'--- {month_names.get(mm, mm)} {yyyy} ---')
        print()

        for day in days:
            pnl = r.daily_pnl.get(day, 0)
            equity += pnl
            month_pnl += pnl
            day_trades = trades_by_day.get(day, [])
            month_trades += len(day_trades)
            month_wins += sum(1 for t in day_trades if t['pnl_dollars'] > 0)

            weekday_names = {0: 'LUN', 1: 'MAR', 2: 'MER', 3: 'JEU', 4: 'VEN', 5: 'SAM', 6: 'DIM'}
            from datetime import date as dt_date
            parts = day.split('-')
            wd = dt_date(int(parts[0]), int(parts[1]), int(parts[2])).weekday()
            wd_str = weekday_names.get(wd, '???')

            if not day_trades:
                print(f'{day} ({wd_str}) :   $      0  {"":30s}  eq=${equity:>+9,.0f}  (pas de trade)')
                continue

            # Day bar
            sign = '+' if pnl >= 0 else '-'
            bar_len = min(int(abs(pnl) / 100), 30)
            bar_char = '#' if pnl >= 0 else '-'
            bar = bar_char * bar_len
            flag = ''
            if pnl < TOPSTEP_LIMIT:
                flag = '  !! TOPSTEP'
            elif pnl < AGENT_LIMIT:
                flag = '  ! >AGENT'

            # Range OPR info
            rh = day_trades[0]['range_high']
            rl = day_trades[0]['range_low']
            rs = day_trades[0]['range_size']

            print(f'{day} ({wd_str}) : {sign}${abs(pnl):>7,.0f}  {bar:<30s}  eq=${equity:>+9,.0f}{flag}')
            print(f'  OPR Range: {rl:.2f} - {rh:.2f} (taille={rs:.0f}pts)')

            # Each trade detail
            for i, t in enumerate(day_trades, 1):
                d = t['direction'].upper()
                entry_h = t['entry_time'][11:16]
                exit_h = t['exit_time'][11:16]
                reason = reason_labels.get(t['exit_reason'], t['exit_reason'])
                w = 'WIN ' if t['pnl_dollars'] > 0 else 'LOSS' if t['pnl_dollars'] < 0 else 'BE  '
                pnl_sign = '+' if t['pnl_dollars'] >= 0 else '-'

                # Compute TP/SL targets
                if t['direction'] == 'long':
                    tp_target = t['entry_price'] + params['tp_long']
                    sl_target = t['entry_price'] - t['sl_pts']
                else:
                    tp_target = t['entry_price'] - params['tp_short']
                    sl_target = t['entry_price'] + t['sl_pts']

                pts_move = t['pnl_points']

                print(f'  Trade {i}: {d:5s} {entry_h} > {exit_h}  '
                      f'{pnl_sign}${abs(t["pnl_dollars"]):>7,.0f}  {w}  ({reason})')
                print(f'           entry={t["entry_price"]:.2f}  exit={t["exit_price"]:.2f}  '
                      f'move={pts_move:+.1f}pts')
                print(f'           TP={tp_target:.2f}  SL={sl_target:.2f} ({t["sl_pts"]:.0f}pts)  '
                      f'bars={t["bars_held"]}')

            print()

        # Month total
        month_wr = month_wins / month_trades * 100 if month_trades else 0
        sign_m = '+' if month_pnl >= 0 else '-'
        print(f'TOTAL {month_names.get(mm, mm)} : {sign_m}${abs(month_pnl):>8,.0f}  '
              f'({month_trades} trades, WR {month_wr:.0f}%)')
        print()
        print()

    # Final summary
    print('=' * 80)
    print('RESUME FINAL')
    print('=' * 80)
    print()

    for month, days in sorted(days_by_month.items()):
        mm = month.split('-')[1]
        yyyy = month.split('-')[0]
        mpnl = sum(r.daily_pnl.get(d, 0) for d in days)
        mt = sum(len(trades_by_day.get(d, [])) for d in days)
        mw = sum(sum(1 for t in trades_by_day.get(d, []) if t['pnl_dollars'] > 0) for d in days)
        mwr = mw / mt * 100 if mt else 0
        sign = '+' if mpnl >= 0 else '-'
        bar = '#' * min(int(abs(mpnl) / 500), 20)
        print(f'  {month_names.get(mm, mm):>10s} {yyyy} : {sign}${abs(mpnl):>8,.0f}  {bar:<20s}  '
              f'{mt} trades  WR {mwr:.0f}%  ({len(days)}j)')

    print()
    print(f'  {"TOTAL":>15s} : +${r.total_pnl_dollars:>8,.0f}')
    print()
    print(f'  Trades       : {r.total_trades} (L:{r.long_trades} S:{r.short_trades})')
    print(f'  Win Rate     : {r.win_rate}%')
    print(f'  Profit Factor: {r.profit_factor}')
    print(f'  Sharpe Ratio : {r.sharpe_ratio}')
    print(f'  Max Drawdown : ${r.max_drawdown:,.0f}')
    print(f'  Proj/mois    : ${np.mean(pnl_vals) * 20:+,.0f}')
    print(f'  J > -$1,800  : {j_agent}')
    print(f'  J > -$4,500  : {j_topstep}')
    print(f'  Jours trades : {len(pnl_vals)} (profit:{sum(1 for p in pnl_vals if p > 0)} '
          f'perte:{sum(1 for p in pnl_vals if p < 0)})')
    print()

    # === ANALYSE WORST DAY ===
    worst_day_key = min(r.daily_pnl, key=r.daily_pnl.get)
    worst_pnl = r.daily_pnl[worst_day_key]
    worst_trades = trades_by_day.get(worst_day_key, [])

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
        print(f'    TP cible : {tp_target:.2f} ({params["tp_short" if t["direction"]=="short" else "tp_long"]}pts = ${params["tp_short" if t["direction"]=="short" else "tp_long"] * 8:.0f})')
        print(f'    SL cible : {sl_target:.2f} ({t["sl_pts"]:.0f}pts = ${t["sl_pts"] * 8:.0f})')
        print(f'    Range    : {t["range_low"]:.2f} - {t["range_high"]:.2f} ({t["range_size"]:.0f}pts)')
        print(f'    Bars held: {t["bars_held"]}')
        print()

    # Simulations: what if...
    print('  WHAT IF ?')
    print('  ---------')

    # 1) What if max_trades was 1 per day?
    if len(worst_trades) > 1:
        first_pnl = worst_trades[0]['pnl_dollars']
        print(f'  Si max 1 trade/jour    : ${first_pnl:+,.0f} (au lieu de ${worst_pnl:+,.0f})')

    # 2) What if SL cap was lower?
    for cap in [150, 175]:
        simul = 0
        for t in worst_trades:
            if t['exit_reason'] == 'sl' and t['sl_pts'] > cap:
                # SL aurait ete plus serre
                capped_loss = cap * 8
                simul -= capped_loss
            else:
                simul += t['pnl_dollars']
        print(f'  Si SL cap {cap}pts       : ${simul:+,.0f} (au lieu de ${worst_pnl:+,.0f})')

    # 3) What if daily_loss_limit was -1500?
    running = 0
    stopped_at = None
    for i, t in enumerate(worst_trades):
        running += t['pnl_dollars']
        if running <= -1500 and stopped_at is None:
            stopped_at = i + 1
            break
    if stopped_at:
        limited_pnl = sum(worst_trades[j]['pnl_dollars'] for j in range(stopped_at))
        print(f'  Si daily limit -$1,500 : ${limited_pnl:+,.0f} (arret apres trade {stopped_at})')

    # 4) What if no second trade after first loss?
    if len(worst_trades) >= 2 and worst_trades[0]['pnl_dollars'] < 0:
        print(f'  Si stop apres 1er loss : ${worst_trades[0]["pnl_dollars"]:+,.0f}')

    print()

    await client.logout()


asyncio.run(run())
