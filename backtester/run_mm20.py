"""
Lance le backtest MM20 sur donnees reelles NQ via ProjectX API.
Usage: python -m backtester.run_mm20 [--days 90]
"""

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

# Path setup
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backtester.mm20_engine import MM20BacktestEngine


async def fetch_data(days: int):
    """Recupere les barres 5min depuis ProjectX API."""
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')

    from projectx_api import ProjectXClient, AggregationUnit, ConnectionURLS

    api_url = os.getenv('PROJECTX_API_URL', 'https://api.topstepx.com')
    username = os.getenv('PROJECTX_USERNAME')
    api_key = os.getenv('PROJECTX_API_KEY')

    if not username or not api_key:
        logger.error("PROJECTX_USERNAME / PROJECTX_API_KEY manquants dans .env")
        return None

    urls = ConnectionURLS(
        api_endpoint=api_url,
        user_hub='https://rtc.topstepx.com/hubs/user',
        market_hub='https://rtc.topstepx.com/hubs/market',
    )
    client = ProjectXClient(urls)

    logger.info(f"Login ProjectX...")
    await client.login({"auth_type": "api_key", "userName": username, "apiKey": api_key})

    contracts = await client.search_for_contracts(searchText="NQ", live=False)
    if not contracts:
        logger.error("Contrat NQ introuvable")
        return None

    contract_id = contracts[0] if isinstance(contracts[0], int) else (contracts[0].get('id') or contracts[0].get('contractId'))
    logger.info(f"Contrat NQ: {contract_id}")

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
        logger.error("Aucune donnee recuperee")
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
    logger.info(f"{len(df)} barres 5min recuperees ({df.index[0]} -> {df.index[-1]})")
    return df


def print_report(report):
    """Affiche le rapport dans le terminal."""
    print("\n" + "=" * 60)
    print("   BACKTEST MM20 TREND FOLLOWING — NQ NASDAQ")
    print("=" * 60)
    print(f"  Trades        : {report.total_trades}")
    print(f"  Win Rate      : {report.win_rate}%")
    print(f"  Wins / Losses : {report.winning_trades} / {report.losing_trades}")
    print(f"  PnL Total     : ${report.total_pnl_usd:+,.2f}")
    print(f"  Avg Win       : ${report.avg_win:+,.2f}")
    print(f"  Avg Loss      : ${report.avg_loss:+,.2f}")
    print(f"  Avg Trade     : ${report.avg_trade:+,.2f}")
    print(f"  Best Trade    : ${report.best_trade:+,.2f}")
    print(f"  Worst Trade   : ${report.worst_trade:+,.2f}")
    print(f"  Profit Factor : {report.profit_factor}")
    print(f"  Max Drawdown  : ${report.max_drawdown_usd:,.2f}")
    print(f"  Sharpe Ratio  : {report.sharpe_ratio}")
    print("-" * 60)

    # Sorties
    exits = {}
    for t in report.trades:
        r = t['exit_reason']
        exits[r] = exits.get(r, 0) + 1
    print("  Sorties       : " + " | ".join(f"{k}: {v}" for k, v in sorted(exits.items())))

    # Directions
    longs = [t for t in report.trades if t['direction'] == 'long']
    shorts = [t for t in report.trades if t['direction'] == 'short']
    long_wr = sum(1 for t in longs if t['pnl_usd'] > 0) / len(longs) * 100 if longs else 0
    short_wr = sum(1 for t in shorts if t['pnl_usd'] > 0) / len(shorts) * 100 if shorts else 0
    print(f"  Long          : {len(longs)} trades, WR {long_wr:.1f}%")
    print(f"  Short         : {len(shorts)} trades, WR {short_wr:.1f}%")

    # PnL par jour (derniers 10)
    print("-" * 60)
    print("  PnL journalier (derniers 10):")
    for d, pnl in sorted(report.daily_pnl.items())[-10:]:
        bar = "+" * int(abs(pnl) / 100) if pnl > 0 else "-" * int(abs(pnl) / 100)
        print(f"    {d}  ${pnl:+8,.2f}  {bar}")

    print("=" * 60 + "\n")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=90, help='Nombre de jours')
    args = parser.parse_args()

    logger.info(f"Backtest MM20 — {args.days} jours")
    df_5min = await fetch_data(args.days)
    if df_5min is None:
        return

    engine = MM20BacktestEngine()
    report = engine.run(df_5min)

    if report is None:
        logger.error("Aucun trade genere")
        return

    print_report(report)

    # Sauvegarde
    from dataclasses import asdict
    data_dir = BASE_DIR / 'data'
    data_dir.mkdir(exist_ok=True)
    out = data_dir / 'backtest_mm20.json'
    out.write_text(json.dumps(asdict(report), default=str, ensure_ascii=False, indent=2), encoding='utf-8')
    logger.info(f"Rapport sauvegarde: {out}")


if __name__ == '__main__':
    asyncio.run(main())
