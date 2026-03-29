"""
Lanceur unique : Dashboard (port 8001) + Bot MM20 Pullback + News
=================================================================
Usage:  python start.py
Stop:   Ctrl+C (ferme proprement les positions ouvertes)
"""

import asyncio
import os
import sys
import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from loguru import logger

os.makedirs('logs', exist_ok=True)
logger.add("logs/start_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days")


async def run_dashboard():
    """Lance le dashboard FastAPI avec TradingBrain (prix live, comptes, etc.)."""
    import importlib

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PARENT_DIR = os.path.join(BASE_DIR, '..')

    # Charge le cerveau Lynnie (optionnel)
    CEO_BRAIN_AVAILABLE = False
    try:
        sys.path.insert(0, PARENT_DIR)
        importlib.import_module('core.autonomous_brain')
        importlib.import_module('core.decision_engine')
        importlib.import_module('memory.memory_manager')
        CEO_BRAIN_AVAILABLE = True
        logger.success("Cerveau Lynnie charge")
        sys.path.remove(PARENT_DIR)
    except Exception as e:
        CEO_BRAIN_AVAILABLE = False
        logger.warning("Cerveau Lynnie non disponible : {}".format(e))
        if PARENT_DIR in sys.path:
            sys.path.remove(PARENT_DIR)

    # Nettoie le module core pour forcer le local
    if 'core' in sys.modules:
        del sys.modules['core']

    sys.path.insert(0, BASE_DIR)
    from core.trading_brain import TradingBrain
    from core.market_analyzer import MarketAnalyzer
    from core.signal_aggregator import SignalAggregator
    from core.strategies import StrategyManager
    from interface.api import start_server

    # Initialise le TradingBrain (connexion API + prix)
    brain = TradingBrain(ceo_brain_available=CEO_BRAIN_AVAILABLE)
    success = await brain.initialize()

    if success:
        logger.success("Dashboard connecte — {} pret".format(brain.instrument))
        analyzer = MarketAnalyzer(
            client=brain.client,
            contract_id=brain.contract_id,
            account_id=brain.account_id
        )
        strategies = StrategyManager()
        aggregator = SignalAggregator(analyzer, strategies)
        brain.analyzer = analyzer
        brain.strategies = strategies
        brain.aggregator = aggregator
        logger.info("Prix {} : {}".format(brain.instrument, brain.current_price))
    else:
        logger.error("Impossible de se connecter — dashboard en mode degrade")

    await start_server(brain)


async def run_bot():
    """Lance le bot MM20 + News."""
    from mm20_news_live import MM20NewsLive

    bot = MM20NewsLive()
    await bot.connect()

    POLL_INTERVAL = 30

    try:
        while True:
            try:
                await bot.check_and_trade()
                try:
                    p = await bot.get_price() if bot.client and bot.contract_id else None
                    bot._save_state(p)
                except Exception:
                    pass
            except Exception as e:
                logger.error("Bot erreur: {}".format(e))
            await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        logger.info("Bot arrete")
        if bot.mm20_in_trade or bot.news_in_trade:
            logger.warning("Position ouverte — fermeture...")
            await bot.close_position()
        if bot.client:
            await bot.client.logout()


async def main():
    logger.info("=" * 50)
    logger.info("TRADING AGENT D6 — MM20 Pullback + News + Dashboard")
    logger.info("=" * 50)

    # Lance les deux en parallele
    dashboard_task = asyncio.create_task(run_dashboard())
    bot_task = asyncio.create_task(run_bot())

    try:
        await asyncio.gather(dashboard_task, bot_task)
    except KeyboardInterrupt:
        pass
    finally:
        bot_task.cancel()
        dashboard_task.cancel()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await dashboard_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("Arret complet.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Ctrl+C — arret.")
