"""
Compare OPR vs MM20 Optimise sur les memes donnees.
Usage: python -m backtester.compare_strategies [--days 90]
"""

import asyncio
import argparse
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import asdict

import pandas as pd
import numpy as np
from loguru import logger

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


async def fetch_data_5min(days: int):
    """Recupere 5min depuis cache ou API."""
    cache = BASE_DIR / 'data' / 'cache_5min.csv'
    if cache.exists():
        logger.info("Cache 5min trouve")
        df = pd.read_csv(cache, index_col='datetime', parse_dates=True)
        return df

    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
    from projectx_api import ProjectXClient, AggregationUnit, ConnectionURLS

    api_url = os.getenv('PROJECTX_API_URL', 'https://api.topstepx.com')
    urls = ConnectionURLS(
        api_endpoint=api_url,
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )
    client = ProjectXClient(urls)
    await client.login({
        "auth_type": "api_key",
        "userName": os.getenv('PROJECTX_USERNAME'),
        "apiKey": os.getenv('PROJECTX_API_KEY'),
    })

    contracts = await client.search_for_contracts(searchText="NQ", live=False)
    cid = contracts[0] if isinstance(contracts[0], int) else (contracts[0].get('id') or contracts[0].get('contractId'))

    now = datetime.utcnow()
    all_bars = []
    for i in range((days // 7) + 1):
        end = now - timedelta(days=i * 7)
        start = end - timedelta(days=7)
        try:
            bars = await client.retrieve_bars(
                contractId=cid, live=False,
                startTime=start, endTime=end,
                unit=AggregationUnit.MINUTE, unitNumber=5,
                limit=10000, includePartialBar=False
            )
            if bars:
                all_bars.extend(bars)
        except Exception as e:
            logger.warning(f"Chunk {i}: {e}")

    await client.logout()

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
    cache.parent.mkdir(exist_ok=True)
    df.to_csv(cache)
    return df


def run_opr(df_5min):
    """Lance le backtest OPR avec les params OPTIMISES (dynSL + SAR)."""
    from backtester.opr_engine import OPREngine

    # Params optimises (depuis opr_dynsl_sar_results.json)
    # Adaptes en NQ 1 lot ($20/pt) pour comparaison fair
    params = {
        'sl_type': 'periods_high_low',
        'sl_long_periods': 9,
        'sl_long_delta': -41.75,
        'sl_short_periods': 15,
        'sl_short_delta': 0.25,
        'tp_long': 217.75,
        'tp_short': 205.75,
        'max_trades': 6,
        'max_longs': 3,
        'max_shorts': 3,
        'min_range': 15,
        'max_range': 999,
        'close_hour': 20,
        'close_min': 49,
        'point_value': 20.0,
        'contracts': 1,
        'sar_enabled': True,
        'auto_dst': True,
        'supertrend_period': 0,
        'daily_loss_limit': -4500,
    }

    engine = OPREngine(params)
    report = engine.run(df_5min, daily_loss_limit=-4500, max_trades_per_day=6)
    return report


def run_mm20_base(df_5min):
    """MM20 config de base (avant optimisation)."""
    from backtester.mm20_engine import MM20BacktestEngine
    engine = MM20BacktestEngine(
        tp_points=200, trail_bars=9, max_trades_day=4, sma_period=20,
        start_offset_min=0, min_sma_dist=0, atr_min=0, daily_loss_stop=0,
    )
    return engine.run(df_5min)


def run_mm20_optimized(df_5min):
    """MM20 config optimisee."""
    from backtester.mm20_engine import MM20BacktestEngine
    engine = MM20BacktestEngine(
        tp_points=300, trail_bars=15, max_trades_day=4, sma_period=20,
        start_offset_min=30, min_sma_dist=20, atr_min=0, daily_loss_stop=2,
    )
    return engine.run(df_5min)


def fmt(v, prefix='$', decimals=0):
    if v is None:
        return '--'
    if prefix == '$':
        return f"${v:+,.{decimals}f}"
    if prefix == '%':
        return f"{v:.{decimals}f}%"
    return f"{v:.{decimals}f}"


def print_comparison(opr, mm20_base, mm20_opt):
    """Affiche le tableau comparatif."""

    # Extraire les metriques
    def metrics(label, report, is_opr=False):
        if report is None:
            return {'label': label, 'trades': 0, 'wr': 0, 'pnl': 0, 'pf': 0,
                    'max_dd': 0, 'avg_trade': 0, 'sharpe': 0,
                    'avg_win': 0, 'avg_loss': 0, 'best': 0, 'worst': 0,
                    'days_prof': 0, 'days_loss': 0}

        if is_opr:
            pnls = [t.pnl_dollars for t in report.trades] if hasattr(report.trades[0], 'pnl_dollars') else [t.get('pnl_dollars', 0) for t in report.trades]
        else:
            pnls = [t['pnl_usd'] for t in report.trades]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        # Daily PnL
        if is_opr:
            daily = report.daily_pnl
        else:
            daily = report.daily_pnl

        days_prof = sum(1 for v in daily.values() if v > 0)
        days_loss = sum(1 for v in daily.values() if v < 0)

        return {
            'label': label,
            'trades': len(pnls),
            'wr': len(wins) / len(pnls) * 100 if pnls else 0,
            'pnl': sum(pnls),
            'pf': abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 99,
            'max_dd': report.max_drawdown if is_opr else report.max_drawdown_usd,
            'avg_trade': np.mean(pnls) if pnls else 0,
            'sharpe': report.sharpe_ratio,
            'avg_win': np.mean(wins) if wins else 0,
            'avg_loss': np.mean(losses) if losses else 0,
            'best': max(pnls) if pnls else 0,
            'worst': min(pnls) if pnls else 0,
            'days_prof': days_prof,
            'days_loss': days_loss,
            'equity': report.equity_curve,
        }

    m_opr = metrics('OPR (Production)', opr, is_opr=True)
    m_base = metrics('MM20 (Base)', mm20_base)
    m_opt = metrics('MM20 (Optimise)', mm20_opt)

    all_m = [m_opr, m_base, m_opt]

    # Header
    w = 22  # column width
    print("\n" + "=" * 82)
    print("   COMPARAISON STRATEGIES — NQ NASDAQ 90 JOURS")
    print("=" * 82)

    header = f"  {'Metrique':<20}"
    for m in all_m:
        header += f" {m['label']:>{w}}"
    print(header)
    print("-" * 82)

    rows = [
        ('Trades', 'trades', '', 0),
        ('Win Rate', 'wr', '%', 1),
        ('PnL Total', 'pnl', '$', 0),
        ('Profit Factor', 'pf', '', 2),
        ('Max Drawdown', 'max_dd', '$', 0),
        ('Avg Trade', 'avg_trade', '$', 0),
        ('Avg Win', 'avg_win', '$', 0),
        ('Avg Loss', 'avg_loss', '$', 0),
        ('Best Trade', 'best', '$', 0),
        ('Worst Trade', 'worst', '$', 0),
        ('Sharpe Ratio', 'sharpe', '', 2),
        ('Jours positifs', 'days_prof', '', 0),
        ('Jours negatifs', 'days_loss', '', 0),
    ]

    for label, key, prefix, dec in rows:
        line = f"  {label:<20}"
        values = [m[key] for m in all_m]
        best_idx = -1

        # Determine best (higher is better, except max_dd, avg_loss, worst, days_loss)
        invert = key in ('max_dd', 'avg_loss', 'worst', 'days_loss')
        if key == 'max_dd':
            # Lower is better
            valid = [abs(v) for v in values if v != 0]
            if valid:
                best_val = min(valid)
                best_idx = [abs(v) for v in values].index(best_val)
        elif invert:
            valid = [v for v in values if v != 0]
            if valid:
                best_val = max(valid)  # least negative
                best_idx = values.index(best_val)
        else:
            valid = [v for v in values]
            if valid:
                best_val = max(valid)
                best_idx = values.index(best_val)

        for i, v in enumerate(values):
            if prefix == '$':
                s = f"${v:+,.{dec}f}"
            elif prefix == '%':
                s = f"{v:.{dec}f}%"
            else:
                s = f"{v:,.{dec}f}"

            marker = ' *' if i == best_idx else '  '
            line += f" {s + marker:>{w}}"

        print(line)

    print("=" * 82)
    print("  * = meilleur sur cette metrique")

    # Ratio risque/rendement
    print("\n  RATIO RENDEMENT / RISQUE :")
    for m in all_m:
        dd = abs(m['max_dd']) if m['max_dd'] else 1
        ratio = m['pnl'] / dd if dd > 0 else 0
        daily_avg = m['pnl'] / max(m['days_prof'] + m['days_loss'], 1)
        print(f"    {m['label']:<22} PnL/MaxDD = {ratio:.2f}x  |  Avg/jour = ${daily_avg:+,.0f}")

    # Topstep compliance
    print("\n  COMPLIANCE TOPSTEP $50K :")
    for m in all_m:
        dd = abs(m['max_dd'])
        ok_dd = dd < 2000
        ok_daily = abs(m['worst']) < 2000  # approx
        status = 'OK' if ok_dd else 'DEPASSE'
        print(f"    {m['label']:<22} MaxDD ${dd:,.0f} [{status}]  |  Pire trade ${m['worst']:+,.0f}")

    print("")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=90)
    args = parser.parse_args()

    logger.info(f"Comparaison strategies — {args.days} jours")

    df_5min = await fetch_data_5min(args.days)
    logger.info(f"{len(df_5min)} barres 5min chargees")

    # Run les 3 strategies
    logger.info("1/3 OPR Production...")
    opr = run_opr(df_5min)
    if opr:
        logger.info(f"  OPR: {opr.total_trades} trades, WR {opr.win_rate}%, PnL ${opr.total_pnl_dollars:+,.0f}")

    logger.info("2/3 MM20 Base...")
    mm20_base = run_mm20_base(df_5min)
    if mm20_base:
        logger.info(f"  MM20 Base: {mm20_base.total_trades} trades, WR {mm20_base.win_rate}%, PnL ${mm20_base.total_pnl_usd:+,.0f}")

    logger.info("3/3 MM20 Optimise...")
    mm20_opt = run_mm20_optimized(df_5min)
    if mm20_opt:
        logger.info(f"  MM20 Opt: {mm20_opt.total_trades} trades, WR {mm20_opt.win_rate}%, PnL ${mm20_opt.total_pnl_usd:+,.0f}")

    print_comparison(opr, mm20_base, mm20_opt)

    # Sauvegarde
    out = {
        'opr': asdict(opr) if opr else None,
        'mm20_base': asdict(mm20_base) if mm20_base else None,
        'mm20_optimized': asdict(mm20_opt) if mm20_opt else None,
    }
    outpath = BASE_DIR / 'data' / 'compare_strategies.json'
    outpath.write_text(json.dumps(out, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
    logger.info(f"Rapport sauvegarde: {outpath}")


if __name__ == '__main__':
    asyncio.run(main())
