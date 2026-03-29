"""
Trading Agent D6 — Agent de trading autonome NQ
Se connecte à TopstepX via projectx-api
Réutilise le cerveau du Lynnie
"""

import sys
import os
import asyncio
import importlib

# Chemin de base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.join(BASE_DIR, '..')

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

# Configure loguru
os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)
logger.add(os.path.join(BASE_DIR, "logs/trading_{time:YYYY-MM-DD}.log"),
           rotation="1 day", retention="30 days")

# Importe le cerveau Lynnie depuis le dossier parent
CEO_BRAIN_AVAILABLE = False
ceo_autonomous_brain = None
ceo_decision_engine = None
ceo_memory_manager = None

try:
    sys.path.insert(0, PARENT_DIR)
    ceo_autonomous_brain = importlib.import_module('core.autonomous_brain')
    ceo_decision_engine = importlib.import_module('core.decision_engine')
    ceo_memory_manager = importlib.import_module('memory.memory_manager')
    CEO_BRAIN_AVAILABLE = True
    logger.success("Cerveau Lynnie charge")
    # Retire le parent du path pour éviter le conflit avec core/ local
    sys.path.remove(PARENT_DIR)
except Exception as e:
    CEO_BRAIN_AVAILABLE = False
    logger.warning(f"Cerveau Lynnie non disponible : {e}")
    if PARENT_DIR in sys.path:
        sys.path.remove(PARENT_DIR)

# Force le rechargement du package core local
if 'core' in sys.modules:
    del sys.modules['core']

# Importe les modules trading locaux
sys.path.insert(0, BASE_DIR)
from core.trading_brain import TradingBrain
from core.market_analyzer import MarketAnalyzer
from core.risk_manager import RiskManager
from core.signal_aggregator import SignalAggregator
from core.strategies import StrategyManager
from core.risk_desk import RiskDeskEngine
from interface.api import start_server, create_risk_desk


async def main():
    logger.info("Trading Agent D6 demarrage...")

    # Initialise le cerveau trading
    brain = TradingBrain(ceo_brain_available=CEO_BRAIN_AVAILABLE)
    success = await brain.initialize()

    if success:
        logger.success(f"Connecte a TopstepX -- {brain.instrument} pret")

        # Initialise les composants
        analyzer = MarketAnalyzer(
            client=brain.client,
            contract_id=brain.contract_id,
            account_id=brain.account_id
        )
        strategies = StrategyManager()
        aggregator = SignalAggregator(analyzer, strategies)

        # Attache au brain (risk_manager deja cree dans TradingBrain.__init__)
        brain.analyzer = analyzer
        brain.strategies = strategies
        brain.aggregator = aggregator

        logger.info(f"Prix {brain.instrument} : {brain.current_price}")
    else:
        logger.error("Impossible de se connecter a TopstepX")
        logger.info("Demarrage en mode degrade (dashboard uniquement)")

    # ── Risk Desk Engine ──
    # Le desk ne prend PAS de trades. Il surveille, calcule le cadre,
    # et persiste tout (trades, PnL, drawdown, progression payout).
    firm = os.getenv('PROP_FIRM', 'topstep')
    plan = os.getenv('TOPSTEP_ACCOUNT_TYPE', '50k')
    instrument = os.getenv('INSTRUMENT', 'MNQ')
    trader_id = os.getenv('TRADER_ID', 'Bass')

    try:
        desk = RiskDeskEngine.create(
            firm=firm,
            plan=plan,
            instrument=instrument,
            trader_id=trader_id,
        )
        create_risk_desk(desk)

        # Si le prix est dispo, alimenter le desk
        if brain and brain.current_price:
            desk.feed_price(brain.current_price)

        logger.success(
            f"Risk Desk ONLINE — {firm} {plan} {instrument} "
            f"(trader: {trader_id})"
        )

        # Auto-reconnect broker si credentials sauvegardes
        try:
            from core.broker_connector import BrokerConnector
            from interface.api import _get_broker, _start_broker_services
            broker = _get_broker()
            reconnected = await broker.auto_reconnect()
            if reconnected:
                await _start_broker_services(broker, instrument)
                logger.success(
                    f"Broker auto-reconnecte: {broker.username} — "
                    f"{len(broker.accounts)} comptes — "
                    f"{len(broker.selected_account_ids)} proteges"
                )
        except Exception as e:
            logger.info(f"Broker auto-reconnect: {e}")

    except Exception as e:
        logger.warning(f"Risk Desk non demarre: {e}")

    # Demarre l'interface dashboard (meme en mode degrade)
    await start_server(brain)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arret Trading Agent D6")
