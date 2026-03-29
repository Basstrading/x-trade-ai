"""
RiskManager Topstep — Multi-comptes (25k/50k/100k/150k/300k)
Regles strictes NON NEGOCIABLES + parametres strategie optimises
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from loguru import logger


@dataclass
class TopstepAccountConfig:
    """Configuration officielle d'un compte Topstep."""
    name: str
    balance: float
    daily_loss_limit: float
    trailing_drawdown: float
    max_contracts: int


# Tous les comptes Topstep disponibles
TOPSTEP_ACCOUNTS = {
    '25k': TopstepAccountConfig(
        name='$25K Combine',
        balance=25_000,
        daily_loss_limit=-1_000,
        trailing_drawdown=-1_500,
        max_contracts=3,
    ),
    '50k': TopstepAccountConfig(
        name='$50K Combine',
        balance=50_000,
        daily_loss_limit=-1_000,
        trailing_drawdown=-2_000,
        max_contracts=5,
    ),
    '100k': TopstepAccountConfig(
        name='$100K Combine',
        balance=100_000,
        daily_loss_limit=-3_000,
        trailing_drawdown=-5_000,
        max_contracts=10,
    ),
    '150k': TopstepAccountConfig(
        name='$150K Combine',
        balance=150_000,
        daily_loss_limit=-4_500,
        trailing_drawdown=-4_500,
        max_contracts=15,
    ),
    '300k': TopstepAccountConfig(
        name='$300K Combine',
        balance=300_000,
        daily_loss_limit=-7_500,
        trailing_drawdown=-7_500,
        max_contracts=20,
    ),
}


def get_account_config(account_type: str) -> TopstepAccountConfig:
    """Retourne la config du compte. account_type: '25k','50k','100k','150k','300k'"""
    config = TOPSTEP_ACCOUNTS.get(account_type.lower())
    if not config:
        raise ValueError(
            f"Compte inconnu : {account_type}. "
            f"Disponibles : {list(TOPSTEP_ACCOUNTS.keys())}"
        )
    return config


@dataclass
class AgentRiskRules:
    """
    Regles de l'agent (conservatrices).
    Calculees automatiquement selon le compte Topstep.
    """
    # Limite agent = 40% de la limite Topstep
    daily_loss_pct: float = 0.40

    # Alerte a 50% de la limite agent
    alert_pct: float = 0.50

    # Reduction taille a 50% limite agent
    reduce_at_pct: float = 0.50

    # Max trades par jour
    max_trades_per_day: int = 4

    # Parametres strategie optimises (backtest 30j NQ)
    stop_fb_points: float = 8.0
    stop_br_points: float = 2.0
    trail_step_points: float = 5.0
    exit_fb_mode: str = 'vpoc'

    # Point value NQ
    point_value: float = 20.0


class RiskManager:
    """
    Gestionnaire de risque Topstep multi-comptes.
    Protege le compte a tout moment.
    """

    def __init__(self, account_type: str = '50k', agent_rules: AgentRiskRules = None):
        # Config compte Topstep
        self.account_config = get_account_config(account_type)
        self.account_type = account_type

        # Regles agent
        self.agent_rules = agent_rules or AgentRiskRules()

        # Calcule limites agent dynamiquement
        # Ex: 50k -> -2000 * 0.40 = -800 | 150k -> -4500 * 0.40 = -1800
        self.agent_daily_limit = self.account_config.daily_loss_limit * self.agent_rules.daily_loss_pct
        self.agent_alert_threshold = self.agent_daily_limit * self.agent_rules.alert_pct
        self.agent_reduce_threshold = self.agent_daily_limit * self.agent_rules.reduce_at_pct

        logger.info(
            f"RiskManager : {self.account_config.name} | "
            f"Daily Topstep: ${self.account_config.daily_loss_limit} | "
            f"Daily agent: ${self.agent_daily_limit:.0f} | "
            f"Trail DD: ${self.account_config.trailing_drawdown}"
        )

        # Etat journalier
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.daily_losses: int = 0
        self.current_date: Optional[date] = None
        self.peak_balance: float = self.account_config.balance
        self.current_balance: float = self.account_config.balance

        # Etat position
        self.position_size: int = 1

        # Historique
        self.trade_history: list = []
        self.daily_history: dict = {}

    def new_day(self):
        """Reset quotidien."""
        today = date.today()
        if self.current_date != today:
            if self.current_date:
                self.daily_history[str(self.current_date)] = {
                    'pnl': self.daily_pnl,
                    'trades': self.daily_trades,
                }
            self.current_date = today
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.daily_losses = 0
            self.position_size = 1
            logger.info(f"Nouveau jour : {today} Balance : ${self.current_balance:.0f}")

    def can_trade(self) -> tuple:
        """Verifie si on peut trader. Retourne (bool, raison)."""
        self.new_day()

        # Regle 1 : Limite journaliere agent
        if self.daily_pnl <= self.agent_daily_limit:
            return False, (
                f"Limite agent atteinte "
                f"(${self.daily_pnl:.0f} / ${self.agent_daily_limit:.0f})"
            )

        # Regle 2 : Limite Topstep absolue
        if self.daily_pnl <= self.account_config.daily_loss_limit:
            return False, f"LIMITE TOPSTEP ${self.account_config.daily_loss_limit}"

        # Regle 3 : Max trades par jour
        if self.daily_trades >= self.agent_rules.max_trades_per_day:
            return False, (
                f"Max trades ({self.daily_trades}/{self.agent_rules.max_trades_per_day})"
            )

        # Regle 4 : Trailing drawdown
        dd = self.current_balance - self.peak_balance
        if dd <= self.account_config.trailing_drawdown:
            return False, f"TRAILING DD TOPSTEP (${dd:.0f})"

        return True, "OK"

    def get_position_size(self) -> int:
        """Retourne la taille de position. Reduit si perte > seuil."""
        if self.daily_pnl <= self.agent_reduce_threshold:
            return 1
        return self.position_size

    def record_trade(self, pnl_dollars: float, direction: str,
                     entry: float, exit_p: float, exit_reason: str):
        """Enregistre un trade termine."""
        self.new_day()
        self.daily_pnl += pnl_dollars
        self.daily_trades += 1
        self.current_balance += pnl_dollars

        if pnl_dollars < 0:
            self.daily_losses += 1

        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        self.trade_history.append({
            'date': str(datetime.now()),
            'pnl': pnl_dollars,
            'direction': direction,
            'entry': entry,
            'exit': exit_p,
            'reason': exit_reason,
            'daily_pnl': self.daily_pnl,
            'balance': self.current_balance,
        })

        # Alertes dynamiques basees sur le compte
        if self.daily_pnl <= self.agent_alert_threshold:
            logger.warning(
                f"Daily P&L : ${self.daily_pnl:.0f} -- "
                f"Attention limite ${self.agent_daily_limit:.0f}"
            )

        if self.daily_pnl <= self.agent_daily_limit * 0.875:
            logger.error(
                f"ALERTE : Daily P&L ${self.daily_pnl:.0f} -- Proche limite !"
            )

        logger.info(
            f"Trade #{self.daily_trades} {direction} "
            f"PnL: ${pnl_dollars:.0f} | "
            f"Jour: ${self.daily_pnl:.0f} | "
            f"Balance: ${self.current_balance:.0f}"
        )

    def get_status(self) -> dict:
        """Retourne l'etat complet du risk."""
        self.new_day()
        can, reason = self.can_trade()
        dd = self.current_balance - self.peak_balance

        daily_pct = (
            abs(self.daily_pnl) / abs(self.agent_daily_limit) * 100
            if self.daily_pnl < 0 else 0
        )

        trailing_dd_pct = (
            abs(dd) / abs(self.account_config.trailing_drawdown) * 100
            if dd < 0 else 0
        )

        return {
            'account_type': self.account_type,
            'account_name': self.account_config.name,
            'can_trade': can,
            'reason': reason,
            'daily_pnl': round(self.daily_pnl, 2),
            'daily_trades': self.daily_trades,
            'daily_losses': self.daily_losses,
            'daily_limit_agent': round(self.agent_daily_limit, 0),
            'daily_limit_topstep': self.account_config.daily_loss_limit,
            'daily_limit_pct': round(daily_pct, 1),
            'agent_remaining': round(self.agent_daily_limit - self.daily_pnl, 0),
            'topstep_remaining': round(self.account_config.daily_loss_limit - self.daily_pnl, 0),
            'position_size': self.get_position_size(),
            'max_contracts': self.account_config.max_contracts,
            'current_balance': round(self.current_balance, 2),
            'peak_balance': round(self.peak_balance, 2),
            'trailing_dd': round(dd, 2),
            'trailing_dd_limit': self.account_config.trailing_drawdown,
            'trailing_dd_pct': round(trailing_dd_pct, 1),
            'strategy_params': {
                'stop_fb': self.agent_rules.stop_fb_points,
                'stop_br': self.agent_rules.stop_br_points,
                'trail_step': self.agent_rules.trail_step_points,
                'exit_fb': self.agent_rules.exit_fb_mode,
            },
        }
