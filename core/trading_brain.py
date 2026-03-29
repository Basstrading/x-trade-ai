"""
TradingBrain — Cerveau central de l'agent trading
Se connecte à TopstepX via projectx-api SDK
Prix temps réel via SignalR (market hub)
"""

from projectx_api import (
    ProjectXClient, Environment, ConnectionURLS,
    OrderSide, OrderType, AggregationUnit
)
from signalrcore.aio.aio_hub_connection_builder import AIOHubConnectionBuilder
from core.risk_manager import RiskManager, AgentRiskRules
from loguru import logger
import os
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# URLs corrigées — le SDK a des URLs périmées
TOPSTEPX_URLS = ConnectionURLS(
    api_endpoint='https://api.topstepx.com',
    user_hub='https://rtc.topstepx.com/hubs/user',
    market_hub='https://rtc.topstepx.com/hubs/market',
)


class TradingBrain:

    def __init__(self, ceo_brain_available: bool = False):
        self.client = None
        self.connected = False
        self.trading_mode = os.getenv('TRADING_MODE', 'paper')
        self.instrument = os.getenv('INSTRUMENT', 'NQ')
        self.account_id = int(os.getenv('ACCOUNT_ID', '26483'))
        self.contract_id = None  # Résolu à l'init
        self.daily_pnl = 0.0
        self.daily_drawdown_limit = float(os.getenv('DAILY_DRAWDOWN_LIMIT', '-1500'))
        self.trades_today = []
        self.is_active = False
        self.ceo_brain_available = ceo_brain_available
        self.start_time = datetime.now()
        self.current_price = None
        self.market_hub = None  # SignalR market hub connection
        self._price_ws_connected = False

        # Parametres optimises backtestes (30j NQ reel, PF 2.15, Sharpe 3.55)
        self.strategy_params = {
            'stop_fb': 8.0,    # Stop fake breakout: 8 pts ($160)
            'stop_br': 2.0,    # Stop breakout: 2 pts ($40)
            'trail_step': 5.0, # Trailing step: 5 pts
            'exit_fb': 'vpoc', # Exit mode: VPOC target
        }

        # Risk manager Topstep multi-comptes (lu depuis .env)
        account_type = os.getenv('TOPSTEP_ACCOUNT_TYPE', '50k')
        self.risk = RiskManager(
            account_type=account_type,
            agent_rules=AgentRiskRules(
                stop_fb_points=self.strategy_params['stop_fb'],
                stop_br_points=self.strategy_params['stop_br'],
                trail_step_points=self.strategy_params['trail_step'],
                exit_fb_mode=self.strategy_params['exit_fb'],
            ),
        )

        # Composants (attaches apres init)
        self.analyzer = None
        self.risk_manager = None  # Legacy — utiliser self.risk
        self.strategies = None
        self.aggregator = None
        self.options_reader = None
        self.options_levels = None
        self.ws_manager = None

        # Charge le cerveau Lynnie si dispo
        self.ceo_brain = None
        if ceo_brain_available:
            try:
                import importlib, sys
                parent_dir = os.path.join(os.path.dirname(__file__), '..', '..')
                sys.path.insert(0, os.path.abspath(parent_dir))
                mod = importlib.import_module('core.autonomous_brain')
                sys.path.remove(os.path.abspath(parent_dir))
                self.ceo_brain = mod.AutonomousBrain()
                logger.info("Cerveau Lynnie connecte")
            except Exception as e:
                logger.warning(f"Lynnie Brain non charge : {e}")
                self.ceo_brain = None

    async def initialize(self):
        """Connexion à TopstepX via projectx-api"""
        try:
            username = os.getenv('PROJECTX_USERNAME', '')
            api_key = os.getenv('PROJECTX_API_KEY', '')

            if not username or not api_key or api_key == '[LA CLÉ API QUE TU AS COPIÉE]':
                logger.error("PROJECTX_USERNAME et PROJECTX_API_KEY requis dans .env")
                return False

            logger.info(f"Connexion TopstepX ({self.instrument})...")

            self.client = ProjectXClient(TOPSTEPX_URLS)
            await self.client.login({
                "auth_type": "api_key",
                "userName": username,
                "apiKey": api_key
            })
            logger.success("Authentification TopstepX réussie")

            # Vérifie le compte
            accounts = await self.client.search_for_account()
            logger.info(f"Comptes disponibles : {len(accounts)}")
            for acc in accounts:
                acc_id = acc.get('id', acc.get('accountId', 'N/A'))
                acc_name = acc.get('name', acc.get('accountName', 'N/A'))
                logger.info(f"  Compte : {acc_id} — {acc_name}")

            # Recherche le contrat NQ
            contracts = await self.client.search_for_contracts(
                searchText=self.instrument, live=False
            )
            if contracts:
                self.contract_id = contracts[0].get('id', contracts[0].get('contractId'))
                contract_name = contracts[0].get('name', contracts[0].get('description', self.instrument))
                logger.info(f"Contrat trouvé : {contract_name} (ID: {self.contract_id})")
            else:
                logger.warning(f"Contrat {self.instrument} non trouvé")

            # Auto-sélection du premier compte actif si account_id invalide
            account_ids = [a.get('id', a.get('accountId')) for a in accounts]
            if self.account_id not in account_ids and accounts:
                self.account_id = account_ids[0]
                logger.warning(f"Account ID ajusté au premier compte : {self.account_id}")

            # Récupère les positions actuelles (peut échouer si compte inactif)
            try:
                positions = await self.client.search_for_positions(accountId=self.account_id)
                logger.info(f"Positions ouvertes : {len(positions)}")
            except Exception as e:
                logger.warning(f"Positions non disponibles : {e}")

            self.connected = True
            logger.success(f"Connecte -- {self.instrument} pret (compte {self.account_id})")

            # Prix initial via REST (fallback)
            try:
                await self._update_price_rest()
            except Exception as e:
                logger.warning(f"Prix initial non disponible : {e}")

            # Connexion SignalR temps réel pour les quotes
            try:
                await self._connect_market_hub()
            except Exception as e:
                logger.warning(f"SignalR market hub non disponible, fallback polling : {e}")

            # Charge les niveaux options depuis hubtrading.fr
            try:
                from core.options_reader import OptionsReader
                self.options_reader = OptionsReader()
                if self.options_reader.db_url:
                    self.options_levels = self.options_reader.get_today_levels()
                    if self.options_levels:
                        n = len(self.options_levels.get_key_levels())
                        logger.info(f"Niveaux options charges : {n} niveaux")
                    else:
                        logger.warning("Aucun niveau options trouve dans la DB")
            except Exception as e:
                logger.warning(f"Options non disponibles : {e}")

            return True

        except Exception as e:
            logger.error(f"Connexion echouee : {e}")
            self.connected = False
            return False

    def _get_auth_token(self):
        """Récupère le token d'authentification depuis le SDK"""
        try:
            return self.client.rest_clients["authentication"]._token
        except (AttributeError, KeyError):
            return None

    async def _connect_market_hub(self):
        """Connecte au market hub SignalR pour les quotes temps réel"""
        token = self._get_auth_token() if self.client else None
        if not token or not self.contract_id:
            logger.warning("SignalR: pas de token ou contract_id — skip")
            return


        # Convertir https:// en wss:// + token dans l'URL (comme tsxapi4py)
        market_url = TOPSTEPX_URLS.market_hub
        if market_url.startswith('https://'):
            wss_url = 'wss://' + market_url[8:]
        else:
            wss_url = market_url
        full_url = f"{wss_url}?access_token={token}"

        self.market_hub = AIOHubConnectionBuilder() \
            .with_url(full_url, options={
                "skip_negotiation": True,
            }) \
            .with_automatic_reconnect({
                "type": "interval",
                "keep_alive_interval": 10,
                "intervals": [0, 2, 5, 10, 30, 60],
            }) \
            .build()

        # Écoute les événements de quote (nom réel: GatewayQuote)
        self.market_hub.on("GatewayQuote", self._on_quote_data)

        self.market_hub.on_open(lambda: logger.info("SignalR market hub connecté"))
        self.market_hub.on_close(lambda: self._on_hub_close())
        self.market_hub.on_error(lambda data: logger.warning(
            f"SignalR market hub erreur : {data}"
        ))

        await self.market_hub.start()
        self._price_ws_connected = True

        # Souscrit aux quotes du contrat
        await self.market_hub.send(
            "SubscribeContractQuotes", [self.contract_id]
        )
        logger.success(
            f"SignalR temps réel activé pour {self.instrument} "
            f"(contrat {self.contract_id})"
        )

    def _on_hub_close(self):
        self._price_ws_connected = False
        logger.warning("SignalR market hub déconnecté")

    def _on_quote_data(self, args):
        """Callback temps réel — appelé à chaque tick de prix (GatewayQuote)"""
        try:
            # Format tsxapi4py: args = [contract_id, quote_dict]
            if isinstance(args, list) and len(args) >= 2:
                quote = args[1]  # Le 2ème élément est le dict de quote
            elif isinstance(args, list) and len(args) == 1:
                quote = args[0]
            else:
                quote = args

            # Log brut pour debug (premières fois seulement)
            if not hasattr(self, '_quote_log_count'):
                self._quote_log_count = 0
            if self._quote_log_count < 3:
                logger.info(f"SignalR quote brut: {str(args)[:400]}")
                self._quote_log_count += 1

            if isinstance(quote, dict):
                # Champs possibles du quote ProjectX/Gateway
                price = (
                    quote.get('lastPrice')
                    or quote.get('last')
                    or quote.get('price')
                    or quote.get('tradePrice')
                    or quote.get('bestBid')
                    or quote.get('bestAsk')
                    or 0
                )
                if price:
                    self.current_price = price
            elif hasattr(quote, 'lastPrice'):
                self.current_price = quote.lastPrice
            elif hasattr(quote, 'last'):
                self.current_price = quote.last

        except Exception as e:
            logger.debug(f"Erreur parsing quote SignalR : {e}")

    async def _update_price_rest(self):
        """Fallback — met à jour le prix via REST (barres 5sec pour moins de lag)"""
        if not self.client or not self.contract_id:
            return None
        try:
            now = datetime.utcnow()
            bars = await self.client.retrieve_bars(
                contractId=self.contract_id,
                live=False,
                startTime=now - timedelta(seconds=30),
                endTime=now,
                unit=AggregationUnit.SECOND,
                unitNumber=5,
                limit=5,
                includePartialBar=True
            )
            if bars and len(bars) > 0:
                last_bar = bars[-1]
                self.current_price = last_bar.get('close', last_bar.get('c', 0))
                logger.debug(f"Prix REST {self.instrument} : {self.current_price}")
            return self.current_price
        except Exception as e:
            logger.debug(f"Erreur prix REST : {e}")
            return self.current_price

    async def get_current_price(self) -> float:
        """Retourne le prix actuel (temps réel si SignalR connecté, sinon REST)"""
        if not self._price_ws_connected:
            await self._update_price_rest()
        return self.current_price or 0

    async def should_enter_trade(self, signal: dict) -> tuple:
        """
        Verifie les conditions d'entree incluant le risk management.
        Retourne (bool, raison).
        """
        # Check risk Topstep avant tout
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            logger.info(f"Trade bloque : {reason}")
            return False, reason

        # Check score signal
        if signal.get('score', 0) < 65:
            return False, "Score trop bas"

        # Ajuste taille position
        size = self.risk.get_position_size()
        signal['size'] = size

        return True, "OK"

    async def on_trade_closed(self, pnl_dollars: float, direction: str,
                              entry: float, exit_p: float, exit_reason: str):
        """Appele quand un trade se ferme."""
        self.risk.record_trade(pnl_dollars, direction, entry, exit_p, exit_reason)

        # Broadcast risk status via WS
        if self.ws_manager:
            try:
                await self.ws_manager({"type": "risk_update", "risk": self.risk.get_status()})
            except Exception:
                pass

    async def start_trading(self):
        """Active le trading autonome"""
        if not self.connected:
            logger.error("Impossible de trader : non connecte")
            return False

        # Check risk Topstep
        can, reason = self.risk.can_trade()
        if not can:
            logger.error(f"Trading bloque par risk : {reason}")
            return False

        # Legacy risk_manager check
        if self.risk_manager and not self.risk_manager.can_trade()[0]:
            reason = self.risk_manager.can_trade()[1]
            logger.error(f"Trading bloque : {reason}")
            return False

        self.is_active = True
        logger.info("Trading ACTIVE")
        return True

    async def stop_trading(self):
        """Désactive le trading — ferme les positions"""
        self.is_active = False
        logger.warning("Trading DÉSACTIVÉ")

        # Ferme les positions ouvertes
        if self.connected and self.client and self.contract_id:
            try:
                positions = await self.client.search_for_positions(
                    accountId=self.account_id
                )
                if positions:
                    for pos in positions:
                        cid = pos.get('contractId', pos.get('contract_id'))
                        if cid:
                            await self.client.close_position(
                                accountId=self.account_id,
                                contractId=str(cid)
                            )
                    logger.info(f"Fermé {len(positions)} position(s)")
            except Exception as e:
                logger.error(f"Erreur fermeture positions : {e}")

        return True

    async def execute_signal(self, signal: dict):
        """Execute un signal de trading via place_order"""
        if not self.is_active:
            return None

        if not self.client or not self.contract_id:
            logger.error("Client ou contrat non disponible")
            return None

        # Check risk Topstep
        can_enter, reason = await self.should_enter_trade(signal)
        if not can_enter:
            logger.warning(f"Signal rejete par risk : {reason}")
            return None

        direction = signal.get('direction', 'long')
        entry = signal.get('entry', 0)
        stop = signal.get('stop', 0)
        target = signal.get('target', 0)
        size = signal.get('size', self.risk.get_position_size())

        side = OrderSide.BUY if direction == 'long' else OrderSide.SELL

        try:
            order = await self.client.place_order(
                accountId=self.account_id,
                contractId=str(self.contract_id),
                type=OrderType.MARKET,
                side=side,
                size=size,
            )

            trade = {
                'time': datetime.now().isoformat(),
                'direction': direction,
                'entry': entry,
                'stop': stop,
                'target': target,
                'size': size,
                'order': order,
                'status': 'open'
            }
            self.trades_today.append(trade)
            logger.info(f"Trade exécuté : {direction.upper()} {size}x @ market")
            return trade

        except Exception as e:
            logger.error(f"Erreur exécution trade : {e}")
            return None

    async def get_positions(self) -> list:
        """Retourne les positions ouvertes"""
        if not self.client:
            return []
        try:
            return await self.client.search_for_positions(accountId=self.account_id)
        except Exception:
            return []

    async def get_open_orders(self) -> list:
        """Retourne les ordres ouverts"""
        if not self.client:
            return []
        try:
            return await self.client.search_for_open_orders(accountId=self.account_id)
        except Exception:
            return []

    async def get_status(self) -> dict:
        """Retourne le statut complet de l'agent"""
        # Rafraichit le prix via REST si SignalR est down (max 1x toutes les 5s)
        if not self._price_ws_connected:
            now = datetime.now()
            last = getattr(self, '_last_rest_price_time', None)
            if not last or (now - last).total_seconds() >= 5:
                try:
                    await self._update_price_rest()
                    self._last_rest_price_time = now
                except Exception:
                    pass
        price = self.current_price

        # Risk Topstep (nouveau)
        risk_topstep = self.risk.get_status()

        # Legacy risk_manager
        risk_legacy = None
        if self.risk_manager:
            risk_legacy = self.risk_manager.get_status()

        return {
            'connected': self.connected,
            'mode': self.trading_mode,
            'instrument': self.instrument,
            'account_id': self.account_id,
            'contract_id': self.contract_id,
            'price': price,
            'daily_pnl': risk_topstep['daily_pnl'],
            'drawdown_limit': self.risk.agent_daily_limit,
            'trades_today': risk_topstep['daily_trades'],
            'trades': self.trades_today[-10:],
            'is_active': self.is_active,
            'ceo_brain': self.ceo_brain is not None,
            'risk': risk_topstep,
            'risk_legacy': risk_legacy,
            'strategy_params': self.strategy_params,
            'uptime': str(datetime.now() - self.start_time).split('.')[0],
        }

    def check_drawdown(self) -> bool:
        """Retourne False si drawdown limite atteinte"""
        if self.daily_pnl <= self.daily_drawdown_limit:
            logger.critical(
                f"DRAWDOWN LIMITE ATTEINTE {self.daily_pnl}$ — ARRÊT TOTAL"
            )
            self.is_active = False
            return False
        return True

    async def disconnect(self):
        """Déconnexion propre"""
        # Ferme le hub SignalR
        if self.market_hub:
            try:
                await self.market_hub.send(
                    "UnsubscribeContractQuotes", [self.contract_id]
                )
                await self.market_hub.stop()
            except Exception:
                pass
            self.market_hub = None
            self._price_ws_connected = False

        if self.client:
            try:
                await self.client.logout()
            except Exception:
                pass
            self.connected = False
            logger.info("Déconnecté de TopstepX")
