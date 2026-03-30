"""
Risk Desk Engine — Institutional Grade
=======================================
Le trader execute. Le Risk Desk definit le cadre.
Pas de discussion. Pas d'override. Pas de "juste cette fois".

Modele : DAYTRADING DISCRETIONNAIRE
- Le trader ne "prepare" pas ses trades. Il voit un setup, il clique.
- Le Risk Desk calcule un CADRE en debut de session :
    taille, stop min, budget risque, nombre de trades restants
- Le trader trade librement dans ce cadre
- Le cadre se met a jour en temps reel (apres chaque trade, vol, circuit breakers)
- Si le cadre dit BLOCKED → le bouton est grise, impossible de trader

Architecture:
    RiskProfile (verrouille a la creation)
        └── RiskDeskEngine
                ├── InstitutionalRiskManager (vol, sizing, circuit breakers)
                ├── PropFirmAccountRules (regles prop firm)
                ├── ConsistencyMonitor (regle de consistance)
                ├── OvernightGuard (protection overnight)
                └── TradingFramework (cadre temps reel)

Usage:
    desk = RiskDeskEngine.create(
        firm="topstep", plan="50k", instrument="MNQ",
    )

    # En continu (chaque seconde, chaque tick) :
    fw = desk.get_framework()
    #   fw.allowed = True
    #   fw.contracts = 3
    #   fw.stop_distance_pts = 15.0
    #   fw.max_loss_per_trade = 90.0
    #   fw.trades_remaining = 5

    # Le trader clique LONG → l'ordre part avec fw.contracts et fw.stop_distance_pts
    # Apres cloture du trade :
    desk.record_trade(pnl=-60, direction="long", entry=21500, exit_price=21490)
    # → Le framework se recalcule automatiquement
"""

from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Dict, List, Tuple
from enum import Enum
from loguru import logger

from core.prop_firm_rules import (
    PropFirmAccountRules, PropFirmType,
    get_prop_firm_rules, create_custom_rules,
)
from core.risk_desk_state import RiskDeskStateManager
from core.institutional_risk_manager import (
    InstitutionalRiskManager, InstrumentSpec, StrategyStats,
    get_instrument, INSTRUMENTS,
    VolRegime, CircuitLevel, SessionPhase,
)
from core.session_config import SessionConfig, ParamMode


# ═══════════════════════════════════════════════════════════════
# TRADING FRAMEWORK — Le cadre temps reel du trader
# ═══════════════════════════════════════════════════════════════

@dataclass
class TradingFramework:
    """
    Le cadre de trading en vigueur MAINTENANT.
    Se recalcule en temps reel. Le trader voit ca et c'est tout.

    Si allowed=True  → le bouton est vert, le trader peut cliquer
    Si allowed=False → le bouton est grise, impossible de trader
    """
    # ── Feu vert / rouge ──
    allowed: bool                       # Peut-on trader maintenant ?
    blocked_reason: str = ""            # Pourquoi c'est bloque (vide si allowed)

    # ── Parametres du prochain trade ──
    contracts: int = 0                  # Taille de position pour le prochain trade
    stop_distance_pts: float = 0.0      # STOP MAX — le trader peut serrer mais PAS elargir
    max_loss_per_trade: float = 0.0     # Perte max en $ si le stop max est touche

    # ── Budget du jour ──
    daily_pnl: float = 0.0             # P&L realise du jour
    daily_peak_pnl: float = 0.0        # Plus haut P&L du jour
    locked_profit: float = 0.0          # Profit verrouille (le trader ne peut pas le rendre)
    min_daily_pnl: float = 0.0          # P&L plancher du jour (ne peut pas descendre en dessous)
    risk_budget_remaining: float = 0.0  # Budget risque restant en $
    trades_today: int = 0               # Trades effectues aujourd'hui
    trades_remaining: int = 0           # Trades restants
    balance: float = 0.0                # Balance courante

    # ── Etat du desk ──
    circuit_level: str = "GREEN"        # GREEN / YELLOW / ORANGE / RED
    vol_regime: str = "normal"          # low / normal / high / extreme
    session: str = ""                   # Phase de session
    atr_5min: float = 0.0              # ATR 5min courant

    # ── Alertes ──
    warnings: List[str] = field(default_factory=list)

    # ── Payout progression ──
    payout_pct: float = 0.0             # % vers le payout
    payout_remaining: float = 0.0       # $ restants
    dd_cushion: float = 0.0             # Marge avant breach DD

    # ── Meta ──
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    instrument: str = ""
    point_value: float = 0.0

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "blocked_reason": self.blocked_reason,
            "contracts": self.contracts,
            "stop_distance_pts": round(self.stop_distance_pts, 2),
            "max_loss_per_trade": round(self.max_loss_per_trade, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_peak_pnl": round(self.daily_peak_pnl, 2),
            "locked_profit": round(self.locked_profit, 2),
            "min_daily_pnl": round(self.min_daily_pnl, 2),
            "risk_budget_remaining": round(self.risk_budget_remaining, 2),
            "trades_today": self.trades_today,
            "trades_remaining": self.trades_remaining,
            "balance": round(self.balance, 2),
            "circuit_level": self.circuit_level,
            "vol_regime": self.vol_regime,
            "session": self.session,
            "atr_5min": round(self.atr_5min, 2),
            "payout_pct": round(self.payout_pct, 1),
            "payout_remaining": round(self.payout_remaining, 2),
            "dd_cushion": round(self.dd_cushion, 2),
            "warnings": self.warnings,
            "timestamp": self.timestamp,
            "instrument": self.instrument,
            "point_value": self.point_value,
        }


# ═══════════════════════════════════════════════════════════════
# CONSISTENCY MONITOR — Regle de consistance (Apex, MFF)
# ═══════════════════════════════════════════════════════════════

class ConsistencyMonitor:
    """
    Aucun jour de profit ne doit representer plus de X% du profit total.
    Si on approche, on reduit la taille.
    """

    def __init__(self, max_pct: float = 0.30, enabled: bool = False):
        self.max_pct = max_pct
        self.enabled = enabled
        self.daily_profits: Dict[str, float] = {}
        self.total_profit: float = 0.0

    def record_day(self, day: date, pnl: float):
        self.daily_profits[str(day)] = pnl
        self.total_profit = sum(
            p for p in self.daily_profits.values() if p > 0
        )

    def get_size_multiplier(self, current_day_pnl: float) -> float:
        """Multiplicateur de taille (1.0 = normal, 0.0 = stop)."""
        if not self.enabled or self.total_profit <= 0:
            return 1.0

        projected_total = self.total_profit + max(0, current_day_pnl)
        if projected_total <= 0:
            return 1.0

        day_ratio = max(0, current_day_pnl) / projected_total

        if day_ratio >= self.max_pct:
            return 0.0
        elif day_ratio >= self.max_pct * 0.80:
            return 0.25
        elif day_ratio >= self.max_pct * 0.60:
            return 0.50
        return 1.0

    def get_warning(self, current_day_pnl: float) -> Optional[str]:
        mult = self.get_size_multiplier(current_day_pnl)
        if mult == 0.0:
            return "Consistance: limite atteinte, trading stoppe"
        elif mult < 1.0:
            return f"Consistance: taille reduite a {mult:.0%}"
        return None


# ═══════════════════════════════════════════════════════════════
# OVERNIGHT GUARD — Protection positions overnight
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# PROFIT PROTECTOR — Protection des gains intraday
# ═══════════════════════════════════════════════════════════════

class ProfitProtector:
    """
    Trailing daily P&L — protege les gains du trader.

    Un trader qui gagne $300 ne doit PAS finir a -$400.
    Le desk verrouille une partie du profit au fur et a mesure.

    Paliers de verrouillage :
    - $0-$150   : pas de lock (le trader a besoin de room)
    - $150-$300 : 40% du peak verrouille
    - $300-$500 : 50% du peak verrouille
    - $500+     : 60% du peak verrouille

    Le plancher P&L = -(budget_restant) mais jamais en dessous du profit locke.
    """

    def __init__(self):
        self.peak_pnl: float = 0.0
        self.locked_profit: float = 0.0
        self.min_pnl: float = 0.0  # Plancher P&L du jour

    def update(self, current_pnl: float, base_daily_limit: float):
        """
        Met a jour apres chaque trade.
        Retourne le nouveau plancher P&L.
        """
        # Mise a jour du peak
        if current_pnl > self.peak_pnl:
            self.peak_pnl = current_pnl

        # Calcul du lock
        if self.peak_pnl >= 500:
            self.locked_profit = self.peak_pnl * 0.60
        elif self.peak_pnl >= 300:
            self.locked_profit = self.peak_pnl * 0.50
        elif self.peak_pnl >= 150:
            self.locked_profit = self.peak_pnl * 0.40
        else:
            self.locked_profit = 0.0

        # Plancher = max entre le base limit et le profit locke
        # base_daily_limit est negatif (ex: -$400)
        # Si locked_profit = $150, le plancher est $150 (pas en dessous)
        if self.locked_profit > 0:
            self.min_pnl = self.locked_profit
        else:
            self.min_pnl = base_daily_limit

        return self.min_pnl

    def is_breached(self, current_pnl: float) -> bool:
        """Le P&L a-t-il franchi le plancher ?"""
        if self.locked_profit > 0:
            return current_pnl < self.locked_profit
        return False

    def reset(self):
        """Reset quotidien."""
        self.peak_pnl = 0.0
        self.locked_profit = 0.0
        self.min_pnl = 0.0

    def get_status(self) -> dict:
        return {
            "peak_pnl": round(self.peak_pnl, 2),
            "locked_profit": round(self.locked_profit, 2),
            "min_pnl": round(self.min_pnl, 2),
        }


class OvernightGuard:
    """Empeche les positions overnight si la prop firm l'interdit."""

    def __init__(self, no_overnight: bool = True, no_weekend: bool = True):
        self.no_overnight = no_overnight
        self.no_weekend = no_weekend

    def can_open_new_position(self) -> Tuple[bool, str]:
        try:
            from zoneinfo import ZoneInfo
            et_now = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            utc_now = datetime.now(timezone.utc)
            et_now = utc_now - timedelta(hours=5)  # EST fallback

        if self.no_weekend and et_now.weekday() in (5, 6):
            return False, "Weekend — marche ferme"

        if self.no_weekend and et_now.weekday() == 4:
            if et_now.time() >= time(15, 50):
                return False, "Weekend imminent"

        if self.no_overnight and et_now.time() >= time(15, 50):
            return False, "Cloture imminente — overnight interdit"

        return True, ""

    def must_close_positions(self) -> Tuple[bool, str]:
        try:
            from zoneinfo import ZoneInfo
            et_now = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            utc_now = datetime.now(timezone.utc)
            et_now = utc_now - timedelta(hours=5)  # EST fallback

        if self.no_overnight and et_now.time() >= time(15, 55):
            return True, "Fermeture obligatoire — overnight interdit"

        if self.no_weekend and et_now.weekday() == 4:
            if et_now.time() >= time(15, 55):
                return True, "Fermeture obligatoire — weekend"

        return False, ""


# ═══════════════════════════════════════════════════════════════
# RISK PROFILE — Configuration verrouillée du trader
# ═══════════════════════════════════════════════════════════════

@dataclass
class RiskProfile:
    """
    Profil de risque — cree par le risk manager (admin),
    PAS par le trader. Verrouille apres creation.
    """
    trader_id: str
    prop_firm_rules: PropFirmAccountRules
    instrument: InstrumentSpec
    # ── Risk model institutionnel adapte prop firm ──
    # 1. Limite de base = X% du DD total (jour normal sans serie de pertes)
    # 2. Reduction progressive apres chaque jour perdant consecutif
    # 3. Cap: jamais plus de Y% du DD RESTANT en un jour
    # 4. Blocked quand DD restant < Z% du total ou 5+ jours perdants
    agent_base_daily_pct: float = 0.20    # 20% du DD total = limite de base
    agent_dd_remaining_cap: float = 0.25  # Jamais plus de 25% du DD restant
    agent_dd_block_pct: float = 0.15      # Bloque quand DD restant < 15% du total
    agent_max_consec_losing_days: int = 5  # Bloque apres 5 jours perdants
    agent_max_trades: int = 6
    agent_max_consecutive_losses: int = 3
    strategy_stats: Optional[StrategyStats] = None
    allowed_sessions: List[SessionPhase] = field(default_factory=lambda: [
        SessionPhase.MORNING,
        SessionPhase.AFTERNOON,
    ])
    # Circuit breaker thresholds (% de la daily limit prop firm)
    cb_yellow_pct: float = 0.25
    cb_orange_pct: float = 0.45
    cb_red_pct: float = 0.60
    # ATR stop multiplier (combien de fois l'ATR pour le stop)
    atr_stop_multiplier: float = 2.0
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    created_by: str = "risk_admin"

    def get_agent_daily_limit(self, current_balance: float, peak_balance: float,
                             consecutive_losing_days: int = 0) -> float:
        """
        Limite agent journaliere — modele institutionnel adapte prop firm.

        3 mecanismes combines :
        1. Base = X% du DD total, reduit apres chaque jour perdant consecutif
        2. Cap = jamais plus de Y% du DD RESTANT
        3. Blocked = si DD restant < Z% du total ou 5+ jours perdants

        Ex: Topstep 50K, DD = $2,000
        - Jour 1 normal: base $400, cap $500 → limite -$400
        - Jour 2 perdant: base $300 (x0.75), cap $400 → limite -$300
        - Jour 3 perdant: base $200 (x0.50), cap $325 → limite -$200
        - Jour 4 perdant: base $100 (x0.25), cap $275 → limite -$100
        - Jour 5 perdant: BLOQUE (trader doit prendre une pause)

        Apres un jour gagnant → reset a la base.
        """
        dd_max = abs(self.prop_firm_rules.max_drawdown)
        dd_used = max(0, peak_balance - current_balance)
        dd_remaining = max(0, dd_max - dd_used)

        # ── Block si DD restant trop bas ──
        if dd_remaining < dd_max * self.agent_dd_block_pct:
            return 0.0  # 0 = bloque

        # ── Block si trop de jours perdants consecutifs ──
        if consecutive_losing_days >= self.agent_max_consec_losing_days:
            return 0.0

        # ── 1. Limite de base (% du DD total) ──
        base = dd_max * self.agent_base_daily_pct

        # Reduction progressive apres jours perdants consecutifs
        # Jour 1 = 100%, Jour 2 = 75%, Jour 3 = 50%, Jour 4 = 25%
        if consecutive_losing_days > 0:
            reduction = max(0.25, 1.0 - (consecutive_losing_days * 0.25))
            base *= reduction

        # ── 2. Cap sur le DD restant ──
        cap = dd_remaining * self.agent_dd_remaining_cap

        # Prend le plus strict des deux
        limit = -min(base, cap)

        # ── 3. Si la firm a une daily limit plus stricte ──
        firm_dll = self.prop_firm_rules.daily_loss_limit
        if firm_dll and firm_dll < 0:
            limit = max(limit, firm_dll)  # max car valeurs negatives

        return limit

    def to_dict(self) -> dict:
        return {
            "trader_id": self.trader_id,
            "firm": self.prop_firm_rules.firm.value,
            "plan": self.prop_firm_rules.plan_name,
            "account_size": self.prop_firm_rules.account_size,
            "instrument": self.instrument.symbol,
            "daily_limit_firm": self.prop_firm_rules.daily_loss_limit,
            "daily_base_pct": self.agent_base_daily_pct,
            "max_drawdown": self.prop_firm_rules.max_drawdown,
            "trailing_drawdown": self.prop_firm_rules.trailing_drawdown,
            "max_contracts": self.prop_firm_rules.max_contracts,
            "max_trades_day": self.agent_max_trades,
            "max_consecutive_losses": self.agent_max_consecutive_losses,
            "overnight_allowed": not self.prop_firm_rules.no_overnight,
            "consistency_rule": self.prop_firm_rules.consistency_rule,
            "atr_stop_multiplier": self.atr_stop_multiplier,
            "circuit_breakers": {
                "yellow": self.cb_yellow_pct,
                "orange": self.cb_orange_pct,
                "red": self.cb_red_pct,
            },
            "allowed_sessions": [s.value for s in self.allowed_sessions],
            "created_at": self.created_at,
        }


# ═══════════════════════════════════════════════════════════════
# RISK DESK ENGINE — Le cadre de trading en temps reel
# ═══════════════════════════════════════════════════════════════

class RiskDeskEngine:
    """
    Risk Desk pour daytrading discretionnaire.

    Le trader ne remplit rien, ne configure rien, ne valide rien.
    Il regarde le cadre (vert/rouge, taille, stop) et il execute.

    Le desk recalcule le cadre en continu :
    - Apres chaque trade ferme (record_trade)
    - Quand la volatilite change (feed_bar)
    - Quand un circuit breaker se declenche
    - Quand la session change (heure)

    Le trader voit :
        "3 MNQ | Stop 15 pts | Budget $450 | 4 trades restants"
    Et c'est tout. Il clique long ou short, ca part.
    """

    def __init__(self, profile: RiskProfile):
        self.profile = profile

        # Institutional Risk Manager (moteur principal)
        self.irm = InstitutionalRiskManager(
            account_size=profile.prop_firm_rules.account_size,
            instrument=profile.instrument.symbol,
            topstep_type=self._resolve_topstep_type(profile),
            strategy_stats=profile.strategy_stats,
            cb_yellow_pct=profile.cb_yellow_pct,
            cb_orange_pct=profile.cb_orange_pct,
            cb_red_pct=profile.cb_red_pct,
            max_trades_per_day=profile.agent_max_trades,
            max_consecutive_losses=profile.agent_max_consecutive_losses,
            allowed_sessions=profile.allowed_sessions,
        )

        # Sous-modules
        self.consistency = ConsistencyMonitor(
            max_pct=profile.prop_firm_rules.consistency_pct,
            enabled=profile.prop_firm_rules.consistency_rule,
        )
        self.overnight = OvernightGuard(
            no_overnight=profile.prop_firm_rules.no_overnight,
            no_weekend=profile.prop_firm_rules.no_weekend,
        )

        # Protection des gains intraday
        self.profit_protector = ProfitProtector()

        # Session Config — AUTO/MANUEL pour chaque parametre
        self.session_config = SessionConfig(
            base_max_trades=profile.agent_max_trades,
            base_atr_mult=profile.atr_stop_multiplier,
        )

        # Jours perdants consecutifs — init a 0, calcule apres le state
        self._consec_losing_days = 0

        # Limite agent dynamique (calculee en temps reel)
        self._get_daily_limit = lambda: profile.get_agent_daily_limit(
            self.irm.current_balance, self.irm.peak_balance,
            self._consec_losing_days,
        )

        # State manager — persistence complete
        self.state = RiskDeskStateManager(trader_id=profile.trader_id)
        self.state.initialize(
            trader_id=profile.trader_id,
            firm=profile.prop_firm_rules.firm.value,
            plan=profile.prop_firm_rules.plan_name,
            instrument=profile.instrument.symbol,
            account_size=profile.prop_firm_rules.account_size,
            trailing_dd_limit=profile.prop_firm_rules.max_drawdown,
            profit_target=profile.prop_firm_rules.profit_target or 0,
            min_trading_days=profile.prop_firm_rules.min_trading_days,
        )

        # Restaurer la balance depuis le state sauvegarde
        if self.state.state.total_trades > 0:
            self.irm.current_balance = self.state.state.current_balance
            self.irm.peak_balance = self.state.state.peak_balance
            self.irm.daily_pnl = self.state.state.today_pnl
            self.irm.daily_trades = self.state.state.today_trades
            self.irm.daily_wins = self.state.state.today_wins
            self.irm.daily_losses = self.state.state.today_losses
            self.irm.consecutive_losses = self.state.state.today_consec_losses
            # CRITIQUE: fixer la date pour empêcher new_day() de reset
            # les compteurs au premier appel. Si on est le même jour
            # que le state sauvegardé, le trader RESTE bloqué.
            from datetime import date as _date
            if self.state.state.today_date == str(_date.today()):
                self.irm.current_date = _date.today()
                # Recalculer le circuit breaker depuis le P&L restauré
                self.irm._update_circuit_breaker()

        # Restaurer le profit protector depuis le state
        if self.state.state.today_peak_pnl > 0:
            self.profit_protector.peak_pnl = self.state.state.today_peak_pnl
            daily_limit = self._get_daily_limit()
            self.profit_protector.update(self.state.state.today_pnl, daily_limit)

        # Historique des trades du jour
        self._trades_today: List[dict] = self.state.state.today_trade_log

        # Maintenant que le state est charge, calculer les jours consecutifs
        self._consec_losing_days = self._calc_consec_losing_days()

        logger.info(
            f"{'='*60}\n"
            f"  RISK DESK — ONLINE\n"
            f"  Trader: {profile.trader_id}\n"
            f"  {profile.prop_firm_rules.firm.value} "
            f"({profile.prop_firm_rules.plan_name})\n"
            f"  Account: ${profile.prop_firm_rules.account_size:,.0f}\n"
            f"  Instrument: {profile.instrument.symbol} "
            f"(${profile.instrument.point_value}/pt)\n"
            f"  Daily Limit: ${self._get_daily_limit():,.0f} "
            f"(firm: ${profile.prop_firm_rules.daily_loss_limit:,.0f})\n"
            f"  ATR Stop: {profile.atr_stop_multiplier}x ATR\n"
            f"  Overnight: {'OK' if not profile.prop_firm_rules.no_overnight else 'BLOCKED'}\n"
            f"{'='*60}"
        )

    def _calc_consec_losing_days(self) -> int:
        """
        Calcule le nombre de jours perdants consecutifs.
        Inclut le jour en cours si le PnL est negatif.
        """
        count = 0

        # Le jour en cours compte si PnL < 0 et au moins 1 trade
        if self.state.state.today_pnl < 0 and self.state.state.today_trades > 0:
            count += 1

        # Jours precedents depuis l'historique
        history = self.state.get_daily_history()
        for day in reversed(history):
            if day.get("pnl", 0) < 0:
                count += 1
            else:
                break
        return count

    def _resolve_topstep_type(self, profile: RiskProfile) -> str:
        size = profile.prop_firm_rules.account_size
        if size <= 25_000:
            return "25k"
        elif size <= 50_000:
            return "50k"
        elif size <= 100_000:
            return "100k"
        elif size <= 150_000:
            return "150k"
        return "300k"

    # ═══════════════════════════════════════════════════════════
    # GET FRAMEWORK — Le coeur du systeme
    # ═══════════════════════════════════════════════════════════

    def get_framework(self) -> TradingFramework:
        """
        Retourne le cadre de trading en vigueur MAINTENANT.

        Appeler en continu (chaque seconde, chaque tick).
        Le trader ne voit que ca. Tout est non negociable.

        Les parametres (taille, stop, trades max, CB) sont calcules
        automatiquement OU overrides par l'admin via session_config.
        Le trader ne choisit rien.
        """
        self.irm.new_day()
        sc = self.session_config
        spec = self.profile.instrument
        rules = self.profile.prop_firm_rules
        warnings = []

        # ── Valeurs effectives (AUTO ou MANUEL selon toggle) ──
        size_mult = sc.effective_size_mult
        atr_mult = sc.effective_atr_mult
        max_trades = sc.effective_max_trades
        cb_strict = sc.effective_cb_strictness

        # ── 1. Peut-on trader ? ──
        allowed = True
        blocked_reason = ""

        # Bloque manuellement par l'admin
        if sc.manually_blocked:
            allowed = False
            blocked_reason = sc.block_reason

        # Check IRM (circuit breakers, session, consec losses, etc.)
        if allowed:
            can_trade, reason, _ = self.irm.can_open_trade()
            if not can_trade:
                allowed = False
                blocked_reason = reason

        # Check max trades (valeur effective AUTO/MANUEL)
        if allowed and self.irm.daily_trades >= max_trades:
            allowed = False
            blocked_reason = f"Max trades atteint ({self.irm.daily_trades}/{max_trades})"

        # Check limite agent (modele institutionnel)
        daily_limit = self._get_daily_limit()
        if allowed and daily_limit == 0:
            allowed = False
            dd_max = abs(self.profile.prop_firm_rules.max_drawdown)
            dd_remaining = dd_max - (self.irm.peak_balance - self.irm.current_balance)
            if dd_remaining < dd_max * self.profile.agent_dd_block_pct:
                blocked_reason = (
                    f"DD restant trop bas (${dd_remaining:,.0f} / ${dd_max:,.0f}) "
                    f"— trading suspendu"
                )
            else:
                blocked_reason = (
                    f"{self._consec_losing_days} jours perdants consecutifs "
                    f"— pause obligatoire"
                )
        elif allowed and self.irm.daily_pnl <= daily_limit:
            allowed = False
            blocked_reason = (
                f"Limite journaliere atteinte "
                f"(${self.irm.daily_pnl:,.0f} / ${daily_limit:,.0f})"
            )

        # Check protection des gains (trailing daily P&L)
        self.profit_protector.update(self.irm.daily_pnl, daily_limit)
        if allowed and self.profit_protector.is_breached(self.irm.daily_pnl):
            allowed = False
            blocked_reason = (
                f"Profit protege — gains verrouilles a "
                f"${self.profit_protector.locked_profit:,.0f}"
            )

        # Check overnight
        if allowed:
            on_ok, on_reason = self.overnight.can_open_new_position()
            if not on_ok:
                allowed = False
                blocked_reason = on_reason

        # Check consistance
        cons_mult = self.consistency.get_size_multiplier(self.irm.daily_pnl)
        if cons_mult == 0.0:
            allowed = False
            blocked_reason = "Regle de consistance: limite atteinte"
        cons_warning = self.consistency.get_warning(self.irm.daily_pnl)
        if cons_warning:
            warnings.append(cons_warning)

        # Check tilt (AUTO)
        if sc.trader.tilt_detected:
            warnings.append(f"TILT: {sc.trader.tilt_reason}")

        # ── 2. Stop distance (ATR x multiplicateur effectif) ──
        atr = self.irm.vol_engine.atr_5min
        stop_distance = atr * atr_mult
        min_stop = spec.tick_size * 4
        stop_distance = max(stop_distance, min_stop)

        # ── 3. Taille de position ──
        contracts = 0
        max_loss = 0.0
        risk_per_contract = stop_distance * spec.point_value

        if allowed and risk_per_contract > 0:
            budget = abs(self._get_daily_limit() - self.irm.daily_pnl)

            # Sizing via IRM (Kelly + vol + circuit breaker)
            current_price = self.irm.current_price or 0
            if current_price > 0:
                fake_stop = current_price - stop_distance
                sizing = self.irm.calculate_position_size(current_price, fake_stop)
                contracts = sizing.get("contracts", 0)
            else:
                balance = self.irm.current_balance
                max_risk = balance * 0.0075
                contracts = max(0, int(max_risk / risk_per_contract))

            # Ajustement taille (AUTO: news, global reduction / MANUEL: override)
            if size_mult < 1.0:
                contracts = max(1, int(contracts * size_mult)) if contracts > 0 else 0
                if sc.position_size_mult.mode == ParamMode.AUTO:
                    warnings.append(
                        f"Taille reduite: {sc.position_size_mult.auto_reason}"
                    )

            # Ajustement consistance
            if cons_mult < 1.0:
                contracts = max(1, int(contracts * cons_mult)) if contracts > 0 else 0

            # Cap prop firm (NON NEGOCIABLE)
            is_micro = spec.symbol.startswith("M") and len(spec.symbol) > 1
            max_firm = rules.max_contracts * (10 if is_micro else 1)
            contracts = min(contracts, max_firm)

            # Cap 2% du compte (NON NEGOCIABLE)
            max_risk_2pct = self.irm.current_balance * 0.02
            max_ct_2pct = int(max_risk_2pct / risk_per_contract) if risk_per_contract > 0 else 0
            if contracts > max_ct_2pct:
                contracts = max(1, max_ct_2pct)

            # Cap budget restant (NON NEGOCIABLE)
            max_ct_budget = int(budget / risk_per_contract) if risk_per_contract > 0 else 0
            if contracts > max_ct_budget:
                contracts = max(1, max_ct_budget) if max_ct_budget > 0 else 0
                if contracts == 0:
                    allowed = False
                    blocked_reason = "Budget risque epuise"

            if allowed and contracts == 0:
                contracts = 1

            max_loss = contracts * risk_per_contract

        # ── 4. Alertes contextuelles ──
        if self.irm.vol_engine.regime == VolRegime.EXTREME:
            warnings.append(f"Volatilite extreme (ATR {atr:.1f})")
        elif self.irm.vol_engine.regime == VolRegime.HIGH:
            warnings.append("Volatilite elevee")

        if sc.news.is_high_impact_day()[0]:
            events = sc.news.is_high_impact_day()[1]
            warnings.append(f"NEWS: {', '.join(events[:2])}")

        if sc.market.regime.value == "choppy":
            warnings.append("Marche choppy — prudence")

        must_close, close_reason = self.overnight.must_close_positions()
        if must_close:
            warnings.append(f"FERMER POSITIONS: {close_reason}")

        # ── Budget & trades restants ──
        budget_remaining = abs(self._get_daily_limit() - self.irm.daily_pnl)
        trades_remaining = max(0, max_trades - self.irm.daily_trades)

        # ── Payout progression ──
        payout = self.state.get_payout_status()

        return TradingFramework(
            allowed=allowed,
            blocked_reason=blocked_reason,
            contracts=contracts,
            stop_distance_pts=round(stop_distance, 2),
            max_loss_per_trade=round(max_loss, 2),
            daily_pnl=round(self.irm.daily_pnl, 2),
            daily_peak_pnl=round(self.profit_protector.peak_pnl, 2),
            locked_profit=round(self.profit_protector.locked_profit, 2),
            min_daily_pnl=round(self.profit_protector.min_pnl, 2),
            risk_budget_remaining=round(budget_remaining, 2),
            trades_today=self.irm.daily_trades,
            trades_remaining=trades_remaining,
            balance=round(self.irm.current_balance, 2),
            circuit_level=self.irm.circuit_level.name,
            vol_regime=self.irm.vol_engine.regime.value,
            session=self.irm.session.get_phase().value,
            atr_5min=round(atr, 2),
            payout_pct=payout["progress_pct"],
            payout_remaining=payout["remaining"],
            dd_cushion=payout["dd_cushion"],
            warnings=warnings,
            instrument=spec.symbol,
            point_value=spec.point_value,
        )

    # ═══════════════════════════════════════════════════════════
    # RECORD TRADE — Apres execution
    # ═══════════════════════════════════════════════════════════

    def record_trade(self, pnl: float, direction: str = "",
                     entry: float = 0, exit_price: float = 0,
                     reason: str = "", contracts: int = 1):
        """
        Enregistre un trade clos. Met a jour le cadre automatiquement.

        Le framework se recalcule : taille, budget, circuit breakers.
        Le prochain appel a get_framework() refletera le nouvel etat.
        """
        circuit = self.irm.record_trade(
            pnl, direction, entry, exit_price, reason, contracts
        )
        self.consistency.record_day(date.today(), self.irm.daily_pnl)

        # Alimenter la detection de tilt + recalcul auto
        self.session_config.record_trade(pnl)

        # Persister le trade (survit au restart)
        self.state.record_trade(
            pnl=pnl, direction=direction,
            entry=entry, exit_price=exit_price,
            contracts=contracts, reason=reason,
            circuit_level=circuit.name,
            vol_regime=self.irm.vol_engine.regime.value,
        )

        self._trades_today.append({
            "time": datetime.now().isoformat(),
            "direction": direction,
            "entry": entry,
            "exit": exit_price,
            "pnl": round(pnl, 2),
            "contracts": contracts,
            "reason": reason,
            "circuit_after": circuit.name,
        })

        # Recalcul jours perdants consecutifs (APRES state)
        self._consec_losing_days = self._calc_consec_losing_days()

        # Log le nouveau cadre
        fw = self.get_framework()
        if fw.allowed:
            logger.info(
                f"CADRE MIS A JOUR | {fw.contracts} contrats | "
                f"Stop {fw.stop_distance_pts} pts | "
                f"Budget ${fw.risk_budget_remaining:,.0f} | "
                f"{fw.trades_remaining} trades restants"
            )
        else:
            logger.warning(
                f"TRADING BLOQUE | {fw.blocked_reason}"
            )

        return fw

    # ═══════════════════════════════════════════════════════════
    # PRICE & VOL FEED
    # ═══════════════════════════════════════════════════════════

    def feed_price(self, price: float, volume: float = 0):
        """Alimente le prix temps reel. Le framework se met a jour."""
        self.irm.feed_price(price, volume)

    def feed_bar(self, high: float, low: float, close: float,
                 open_p: float = 0, timeframe: str = "5min"):
        """Alimente une bougie. L'ATR, le stop et le regime se recalculent."""
        self.irm.feed_bar(high, low, close, timeframe)
        # Alimenter le detecteur de regime (open est optionnel, fallback sur close)
        self.session_config.feed_bar(open_p or close, high, low, close)

    # ═══════════════════════════════════════════════════════════
    # SYNC FROM BROKER
    # ═══════════════════════════════════════════════════════════

    def sync_from_broker(self, broker_balance: float):
        """
        Synchronise le Risk Desk avec la balance réelle du broker.
        À appeler à chaque reconnexion et périodiquement.

        - Met à jour la balance dans le state (persiste)
        - Met à jour la balance dans l'IRM (RAM)
        - Recalcule le P&L du jour si le state n'a pas de trades
          (le trader a tradé pendant que le Risk Desk était off)
        """
        old_balance = self.irm.current_balance

        # 1. Sync le state persistant
        self.state.sync_balance(broker_balance)

        # 2. Sync l'IRM en RAM
        self.irm.current_balance = broker_balance
        if broker_balance > self.irm.peak_balance:
            self.irm.peak_balance = broker_balance

        # 3. Si le P&L du jour est 0 mais la balance a bougé
        #    → le trader a tradé sans le Risk Desk
        #    → recalculer le daily P&L depuis la balance d'ouverture
        if self.irm.daily_trades == 0 and abs(broker_balance - old_balance) > 0.50:
            # Retrouver la balance d'ouverture du jour
            opening = self.state.state.initial_balance + (
                self.state.state.total_profit - self.state.state.today_pnl
            )
            inferred_pnl = broker_balance - opening
            if abs(inferred_pnl) > 0.50:
                self.irm.daily_pnl = inferred_pnl
                self.state.state.today_pnl = inferred_pnl
                self.state._save()
                logger.warning(
                    f"SYNC: P&L du jour recalcule depuis broker: "
                    f"${inferred_pnl:+,.2f} (balance {old_balance:,.0f} -> "
                    f"{broker_balance:,.0f})"
                )

        if abs(broker_balance - old_balance) > 0.01:
            logger.info(
                f"SYNC: Balance {old_balance:,.2f} -> {broker_balance:,.2f}"
            )

    # ═══════════════════════════════════════════════════════════
    # KILL SWITCH & OVERNIGHT
    # ═══════════════════════════════════════════════════════════

    async def kill_all(self, client, account_id: int) -> dict:
        """KILL SWITCH — Coupe tout. Immediat."""
        return await self.irm.kill_all_positions(client, account_id)

    async def check_and_kill(self, client, account_id: int) -> bool:
        """Verifie et declenche le kill switch si necessaire."""
        return await self.irm.check_and_kill_if_needed(client, account_id)

    async def enforce_blocks(self, client, account_id: int) -> int:
        """
        Si le framework dit BLOQUÉ, annule les ordres pending.
        Ne touche PAS aux positions ouvertes.
        À appeler périodiquement (chaque tick / chaque seconde).
        Retourne le nombre d'ordres annulés.
        """
        fw = self.get_framework()
        if not fw.allowed:
            cancelled = await self.irm.cancel_pending_orders(client, account_id)
            if cancelled > 0:
                logger.warning(
                    f"ENFORCE: {cancelled} ordres annulés — {fw.blocked_reason}"
                )
            return cancelled
        return 0

    def must_close_overnight(self) -> Tuple[bool, str]:
        """Doit-on fermer les positions maintenant ?"""
        return self.overnight.must_close_positions()

    # ═══════════════════════════════════════════════════════════
    # VUES
    # ═══════════════════════════════════════════════════════════

    def get_admin_view(self) -> dict:
        """Vue complete pour le risk manager — le trader n'y a PAS acces."""
        irm_status = self.irm.get_status()
        must_close, close_reason = self.overnight.must_close_positions()

        return {
            "profile": self.profile.to_dict(),
            "irm": irm_status,
            "session_config": self.session_config.to_dict(),
            "payout": self.state.get_payout_status(),
            "stats": self.state.get_stats(),
            "today": self.state.get_today(),
            "daily_history": self.state.get_daily_history()[-30:],
            "consistency": {
                "enabled": self.consistency.enabled,
                "daily_profits": self.consistency.daily_profits,
                "total_profit": round(self.consistency.total_profit, 2),
            },
            "overnight": {
                "must_close": must_close,
                "reason": close_reason,
            },
            "trades_today": self._trades_today,
        }

    def get_status(self) -> dict:
        """Status rapide pour l'API."""
        fw = self.get_framework()
        return {
            "online": True,
            "trader": self.profile.trader_id,
            "firm": self.profile.prop_firm_rules.firm.value,
            "plan": self.profile.prop_firm_rules.plan_name,
            "instrument": self.profile.instrument.symbol,
            "allowed": fw.allowed,
            "contracts": fw.contracts,
            "stop_distance_pts": fw.stop_distance_pts,
            "circuit_level": fw.circuit_level,
            "daily_pnl": fw.daily_pnl,
            "trades_remaining": fw.trades_remaining,
            "balance": fw.balance,
        }

    # ═══════════════════════════════════════════════════════════
    # FACTORY
    # ═══════════════════════════════════════════════════════════

    @classmethod
    def create(
        cls,
        firm: str = "topstep",
        plan: str = "50k",
        instrument: str = "MNQ",
        trader_id: str = "trader_1",
        strategy_stats: StrategyStats = None,
        **profile_overrides,
    ) -> "RiskDeskEngine":
        """
        Factory — cree un Risk Desk en une ligne.

        Exemples:
            desk = RiskDeskEngine.create("topstep", "50k", "MNQ")
            desk = RiskDeskEngine.create("apex", "100k", "CL")
            desk = RiskDeskEngine.create("ftmo", "100k", "6E")
        """
        rules = get_prop_firm_rules(firm, plan)
        spec = get_instrument(instrument)

        profile = RiskProfile(
            trader_id=trader_id,
            prop_firm_rules=rules,
            instrument=spec,
            strategy_stats=strategy_stats,
            **profile_overrides,
        )

        return cls(profile)

    @classmethod
    def create_custom(
        cls,
        plan_name: str,
        account_size: float,
        daily_loss_limit: float,
        max_drawdown: float,
        instrument: str = "MNQ",
        trader_id: str = "trader_1",
        **kwargs,
    ) -> "RiskDeskEngine":
        """Factory pour un compte custom / prop firm non supportee."""
        custom_rule_keys = (
            'trailing_drawdown', 'max_contracts', 'max_position_size',
            'profit_target', 'no_overnight', 'no_weekend',
            'no_news_trading', 'consistency_rule', 'consistency_pct',
            'min_trading_days', 'daily_trade_limit',
        )
        profile_keys = (
            'agent_base_daily_pct', 'agent_dd_remaining_cap',
            'agent_dd_block_pct', 'agent_max_consec_losing_days',
            'agent_max_trades',
            'agent_max_consecutive_losses', 'allowed_sessions',
            'cb_yellow_pct', 'cb_orange_pct', 'cb_red_pct',
            'atr_stop_multiplier',
        )

        rules = create_custom_rules(
            plan_name=plan_name,
            account_size=account_size,
            daily_loss_limit=daily_loss_limit,
            max_drawdown=max_drawdown,
            **{k: v for k, v in kwargs.items() if k in custom_rule_keys},
        )
        spec = get_instrument(instrument)

        profile = RiskProfile(
            trader_id=trader_id,
            prop_firm_rules=rules,
            instrument=spec,
            **{k: v for k, v in kwargs.items() if k in profile_keys},
        )

        return cls(profile)
