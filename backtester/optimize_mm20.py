"""
Optimiseur MM20 — teste des combinaisons de parametres
pour trouver la meilleure config.
Usage: python -m backtester.optimize_mm20 [--days 90]
"""

import asyncio
import argparse
import json
import sys
import itertools
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Optional

import pandas as pd
import numpy as np
import pytz
from loguru import logger

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backtester.opr_engine import is_dst_gap

POINT_VALUE = 20.0
PARIS = pytz.timezone('Europe/Paris')


@dataclass
class Params:
    sma_period: int = 20
    trail_bars: int = 9
    tp_points: float = 200.0
    max_trades_day: int = 4
    start_offset_min: int = 0       # minutes apres 15h30 (0=15h30, 15=15h45, 30=16h00)
    min_sma_dist: float = 0.0       # distance min prix-SMA en points pour entrer
    max_sma_dist: float = 0.0       # distance max prix-SMA (0=pas de cap)
    atr_min: float = 0.0            # ATR 14 5min minimum pour entrer
    daily_loss_stop: int = 0        # stop apres N pertes consecutives (0=off)
    use_atr_trail: bool = False     # trailing stop = ATR * multiplier au lieu de N bars
    atr_trail_mult: float = 2.0     # multiplier ATR pour trailing


def backtest_params(df_5min: pd.DataFrame, df_1h: pd.DataFrame, p: Params) -> dict:
    """Backtest rapide avec un set de params. Retourne un dict de metriques."""
    df = df_5min
    sma5 = df['close'].rolling(p.sma_period).mean()
    sma1h = df_1h['sma20']

    # ATR 14 sur 5min
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()

    # Merge
    sma1h_ff = sma1h.reindex(df.index, method='ffill')
    close1h_ff = df_1h['close'].reindex(df.index, method='ffill')
    atr_ff = atr14

    trades = []
    equity = [0.0]

    dates = df['date'].unique()

    for day in dates:
        mask = df['date'] == day
        idx = df.index[mask]
        if len(idx) < p.sma_period + 10:
            continue

        dst = is_dst_gap(day)
        base_h, base_m = (14, 30) if dst else (15, 30)
        close_h, close_m = (19, 39) if dst else (20, 39)

        # Offset start
        start_total_min = base_h * 60 + base_m + p.start_offset_min
        start_h = start_total_min // 60
        start_m = start_total_min % 60

        trades_today = 0
        consec_losses = 0
        in_trade = False
        direction = ''
        entry_price = 0.0
        trail_stop = 0.0
        entry_time = ''

        for j, ts in enumerate(idx):
            row = df.loc[ts]
            ph, pm = row['paris_h'], row['paris_m']

            if (ph < start_h) or (ph == start_h and pm < start_m):
                continue

            # Time exit
            if in_trade and ((ph > close_h) or (ph == close_h and pm >= close_m)):
                pnl = (row['close'] - entry_price) if direction == 'long' else (entry_price - row['close'])
                trades.append({'pnl': round(pnl * POINT_VALUE, 2), 'dir': direction, 'exit': 'time', 'date': str(day)})
                equity.append(equity[-1] + trades[-1]['pnl'])
                if pnl < 0:
                    consec_losses += 1
                else:
                    consec_losses = 0
                in_trade = False
                continue

            if (ph > close_h) or (ph == close_h and pm >= close_m):
                continue

            # Manage open position
            if in_trade:
                if direction == 'long':
                    # TP
                    if row['high'] >= entry_price + p.tp_points:
                        pnl = p.tp_points
                        trades.append({'pnl': round(pnl * POINT_VALUE, 2), 'dir': direction, 'exit': 'tp', 'date': str(day)})
                        equity.append(equity[-1] + trades[-1]['pnl'])
                        consec_losses = 0
                        in_trade = False
                        continue

                    # Trail stop
                    if p.use_atr_trail:
                        cur_atr = atr_ff.get(ts, 20)
                        if not np.isnan(cur_atr):
                            new_stop = row['close'] - cur_atr * p.atr_trail_mult
                            trail_stop = max(trail_stop, new_stop)
                    else:
                        start_i = max(0, j - p.trail_bars)
                        window = df.loc[idx[start_i:j + 1], 'low']
                        new_stop = window.min()
                        trail_stop = max(trail_stop, new_stop) if trail_stop > 0 else new_stop

                    if row['low'] <= trail_stop:
                        pnl = trail_stop - entry_price
                        trades.append({'pnl': round(pnl * POINT_VALUE, 2), 'dir': direction, 'exit': 'trail', 'date': str(day)})
                        equity.append(equity[-1] + trades[-1]['pnl'])
                        if pnl < 0:
                            consec_losses += 1
                        else:
                            consec_losses = 0
                        in_trade = False
                        continue
                else:
                    # TP short
                    if row['low'] <= entry_price - p.tp_points:
                        pnl = p.tp_points
                        trades.append({'pnl': round(pnl * POINT_VALUE, 2), 'dir': direction, 'exit': 'tp', 'date': str(day)})
                        equity.append(equity[-1] + trades[-1]['pnl'])
                        consec_losses = 0
                        in_trade = False
                        continue

                    # Trail stop short
                    if p.use_atr_trail:
                        cur_atr = atr_ff.get(ts, 20)
                        if not np.isnan(cur_atr):
                            new_stop = row['close'] + cur_atr * p.atr_trail_mult
                            trail_stop = min(trail_stop, new_stop) if trail_stop > 0 else new_stop
                    else:
                        start_i = max(0, j - p.trail_bars)
                        window = df.loc[idx[start_i:j + 1], 'high']
                        new_stop = window.max()
                        trail_stop = min(trail_stop, new_stop) if trail_stop > 0 else new_stop

                    if row['high'] >= trail_stop:
                        pnl = entry_price - trail_stop
                        trades.append({'pnl': round(pnl * POINT_VALUE, 2), 'dir': direction, 'exit': 'trail', 'date': str(day)})
                        equity.append(equity[-1] + trades[-1]['pnl'])
                        if pnl < 0:
                            consec_losses += 1
                        else:
                            consec_losses = 0
                        in_trade = False
                        continue
                continue

            # --- Entry logic ---
            if trades_today >= p.max_trades_day:
                continue
            if p.daily_loss_stop > 0 and consec_losses >= p.daily_loss_stop:
                continue

            s5 = sma5.get(ts)
            s1h = sma1h_ff.get(ts)
            c1h = close1h_ff.get(ts)
            cur_atr = atr_ff.get(ts)

            if s5 is None or s1h is None or c1h is None:
                continue
            if np.isnan(s5) or np.isnan(s1h) or np.isnan(c1h):
                continue

            # ATR filter
            if p.atr_min > 0 and (np.isnan(cur_atr) or cur_atr < p.atr_min):
                continue

            close_5 = row['close']

            # Distance from SMA filter
            dist = abs(close_5 - s5)
            if p.min_sma_dist > 0 and dist < p.min_sma_dist:
                continue
            if p.max_sma_dist > 0 and dist > p.max_sma_dist:
                continue

            new_dir = None
            if close_5 > s5 and c1h > s1h:
                new_dir = 'long'
            elif close_5 < s5 and c1h < s1h:
                new_dir = 'short'

            if new_dir:
                direction = new_dir
                entry_price = close_5
                entry_time = str(ts)

                # Init trail
                start_i = max(0, j - p.trail_bars)
                window_idx = idx[start_i:j + 1]
                if new_dir == 'long':
                    if p.use_atr_trail and not np.isnan(cur_atr):
                        trail_stop = close_5 - cur_atr * p.atr_trail_mult
                    else:
                        trail_stop = df.loc[window_idx, 'low'].min()
                else:
                    if p.use_atr_trail and not np.isnan(cur_atr):
                        trail_stop = close_5 + cur_atr * p.atr_trail_mult
                    else:
                        trail_stop = df.loc[window_idx, 'high'].max()

                in_trade = True
                trades_today += 1

        # EOD close
        if in_trade:
            last = df.loc[idx[-1]]
            pnl = (last['close'] - entry_price) if direction == 'long' else (entry_price - last['close'])
            trades.append({'pnl': round(pnl * POINT_VALUE, 2), 'dir': direction, 'exit': 'eod', 'date': str(day)})
            equity.append(equity[-1] + trades[-1]['pnl'])
            in_trade = False

    if not trades:
        return None

    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_w = sum(wins) if wins else 0
    gross_l = abs(sum(losses)) if losses else 0

    eq = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = abs(dd.min())

    return {
        'trades': len(trades),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'pnl': round(sum(pnls), 2),
        'avg_trade': round(np.mean(pnls), 2),
        'avg_win': round(np.mean(wins), 2) if wins else 0,
        'avg_loss': round(np.mean(losses), 2) if losses else 0,
        'pf': round(gross_w / gross_l, 2) if gross_l > 0 else 99,
        'max_dd': round(max_dd, 2),
        'sharpe': round(np.mean(pnls) / np.std(pnls) * np.sqrt(252), 2) if np.std(pnls) > 0 else 0,
        'exits': {r: sum(1 for t in trades if t['exit'] == r) for r in ['tp', 'trail', 'time', 'eod']},
        'equity': equity,
        'trades_list': trades,
    }


def prepare_data(df_5min_raw: pd.DataFrame):
    """Pre-calcule les colonnes communes pour accelerer l'optimisation."""
    df = df_5min_raw.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')

    paris = df.index.tz_convert(PARIS)
    df['paris_h'] = paris.hour
    df['paris_m'] = paris.minute
    df['date'] = paris.date

    # Resample 1h
    df_1h = df.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    if df_1h.index.tz is None:
        df_1h.index = df_1h.index.tz_localize('UTC')
    df_1h['sma20'] = df_1h['close'].rolling(20).mean()

    return df, df_1h


async def fetch_data(days: int):
    """Meme fetch que run_mm20.py."""
    from dotenv import load_dotenv
    import os
    load_dotenv(BASE_DIR / '.env')

    from projectx_api import ProjectXClient, AggregationUnit, ConnectionURLS

    api_url = os.getenv('PROJECTX_API_URL', 'https://api.topstepx.com')
    username = os.getenv('PROJECTX_USERNAME')
    api_key = os.getenv('PROJECTX_API_KEY')

    urls = ConnectionURLS(
        api_endpoint=api_url,
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )
    client = ProjectXClient(urls)
    await client.login({"auth_type": "api_key", "userName": username, "apiKey": api_key})

    contracts = await client.search_for_contracts(searchText="NQ", live=False)
    contract_id = contracts[0] if isinstance(contracts[0], int) else (contracts[0].get('id') or contracts[0].get('contractId'))

    now = datetime.utcnow()
    all_bars = []
    n_chunks = (days // 7) + 1

    for chunk_i in range(n_chunks):
        chunk_end = now - timedelta(days=chunk_i * 7)
        chunk_start = chunk_end - timedelta(days=7)
        logger.info(f"  Semaine {chunk_i + 1}/{n_chunks}...")
        try:
            bars = await client.retrieve_bars(
                contractId=contract_id, live=False,
                startTime=chunk_start, endTime=chunk_end,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=10000, includePartialBar=False
            )
            if bars:
                all_bars.extend(bars)
        except Exception as e:
            logger.warning(f"  Chunk {chunk_i}: {e}")

    await client.logout()

    if not all_bars:
        return None

    data = []
    for b in all_bars:
        d = b if isinstance(b, dict) else b.__dict__
        dt = d.get('t') or d.get('timestamp') or d.get('datetime')
        data.append({
            'datetime': dt,
            'open': float(d.get('o') or d.get('open') or 0),
            'high': float(d.get('h') or d.get('high') or 0),
            'low': float(d.get('l') or d.get('low') or 0),
            'close': float(d.get('c') or d.get('close') or 0),
            'volume': float(d.get('v') or d.get('volume') or 1),
        })

    df = pd.DataFrame(data)
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    df = df.sort_values('datetime').drop_duplicates('datetime').set_index('datetime')
    return df


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=90)
    args = parser.parse_args()

    logger.info(f"Optimisation MM20 — {args.days} jours")

    # Check cache
    cache_path = BASE_DIR / 'data' / 'cache_5min.csv'
    if cache_path.exists():
        logger.info("Cache trouve, chargement...")
        df_raw = pd.read_csv(cache_path, index_col='datetime', parse_dates=True)
    else:
        logger.info("Telechargement donnees...")
        df_raw = await fetch_data(args.days)
        if df_raw is None:
            logger.error("Pas de donnees")
            return
        cache_path.parent.mkdir(exist_ok=True)
        df_raw.to_csv(cache_path)
        logger.info(f"Cache sauvegarde: {cache_path}")

    logger.info(f"{len(df_raw)} barres 5min")
    df, df_1h = prepare_data(df_raw)

    # ===== GRID DE PARAMETRES =====
    # Base = meilleure config precedente, on explore max_sma_dist
    grid = {
        'trail_bars':       [12, 15, 18],
        'tp_points':        [250, 300, 400],
        'start_offset_min': [15, 30],
        'min_sma_dist':     [15, 20],
        'max_sma_dist':     [0, 40, 60, 80, 100, 150],
        'daily_loss_stop':  [2, 3],
        'atr_min':          [0],
    }

    keys = list(grid.keys())
    combos = list(itertools.product(*grid.values()))
    total = len(combos)
    logger.info(f"Test de {total} combinaisons...")

    results = []
    baseline = Params()

    for i, combo in enumerate(combos):
        p = Params(**dict(zip(keys, combo)))

        res = backtest_params(df, df_1h, p)
        if res is None:
            continue

        results.append({
            'params': dict(zip(keys, combo)),
            'trades': res['trades'],
            'win_rate': res['win_rate'],
            'pnl': res['pnl'],
            'avg_trade': res['avg_trade'],
            'pf': res['pf'],
            'max_dd': res['max_dd'],
            'sharpe': res['sharpe'],
            'exits': res['exits'],
        })

        if (i + 1) % 100 == 0:
            logger.info(f"  {i + 1}/{total} combos testees...")

    logger.info(f"{len(results)} combos valides sur {total}")

    # Tri par score composite : PnL * PF / max_dd (favorise les strats rentables et stables)
    for r in results:
        dd = max(r['max_dd'], 1)
        r['score'] = round(r['pnl'] * r['pf'] / dd, 2)

    results.sort(key=lambda x: x['score'], reverse=True)

    # Affiche top 10
    print("\n" + "=" * 90)
    print("  TOP 10 CONFIGURATIONS MM20")
    print("=" * 90)
    print(f"  {'#':>3}  {'PnL':>10}  {'WR':>6}  {'PF':>5}  {'MaxDD':>9}  {'Trades':>6}  {'Avg':>8}  {'Sharpe':>6}  Params")
    print("-" * 90)

    for i, r in enumerate(results[:10]):
        p = r['params']
        param_str = f"trail={p['trail_bars']} tp={p['tp_points']} start=+{p['start_offset_min']}m sma_dist={p['min_sma_dist']} loss_stop={p['daily_loss_stop']} atr_min={p['atr_min']}"
        print(f"  {i+1:>3}  ${r['pnl']:>+9,.0f}  {r['win_rate']:>5.1f}%  {r['pf']:>5.2f}  ${r['max_dd']:>8,.0f}  {r['trades']:>6}  ${r['avg_trade']:>+7,.0f}  {r['sharpe']:>6.2f}  {param_str}")

    print("=" * 90)

    # Baseline comparison
    base_res = backtest_params(df, df_1h, baseline)
    if base_res:
        print(f"\n  BASELINE (config originale):")
        print(f"  PnL: ${base_res['pnl']:+,.0f} | WR: {base_res['win_rate']}% | PF: {base_res['pf']} | MaxDD: ${base_res['max_dd']:,.0f} | Trades: {base_res['trades']} | Sharpe: {base_res['sharpe']}")

    best = results[0] if results else None
    if best:
        print(f"\n  MEILLEURE CONFIG:")
        for k, v in best['params'].items():
            print(f"    {k}: {v}")
        print(f"  Sorties: {best['exits']}")

    # Sauvegarde
    out = BASE_DIR / 'data' / 'optimization_mm20.json'
    out.write_text(json.dumps(results[:20], default=str, ensure_ascii=False, indent=2), encoding='utf-8')
    logger.info(f"Top 20 sauvegarde: {out}")

    # Backtest complet avec la meilleure config + sauvegarde
    if best:
        from backtester.mm20_engine import MM20BacktestEngine
        best_p = best['params']
        engine = MM20BacktestEngine(
            tp_points=best_p['tp_points'],
            trail_bars=best_p['trail_bars'],
            max_trades_day=4,
            sma_period=20,
        )
        # Re-run full engine pour le rapport detaille
        report = engine.run(df_raw)
        if report:
            report_dict = asdict(report)
            report_dict['optimized_params'] = best_p
            (BASE_DIR / 'data' / 'backtest_mm20_optimized.json').write_text(
                json.dumps(report_dict, default=str, ensure_ascii=False, indent=2), encoding='utf-8'
            )
            logger.info("Rapport optimise sauvegarde dans data/backtest_mm20_optimized.json")


if __name__ == '__main__':
    asyncio.run(main())
