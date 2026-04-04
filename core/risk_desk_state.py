"""
Risk Desk State — Persistence complete
========================================
Le systeme se souvient de TOUT :
- Trades (chaque trade, chaque jour)
- PnL journalier et cumule
- Peak balance et trailing drawdown
- Progression vers le payout
- Circuit breakers et historique

Au redemarrage, le desk reprend EXACTEMENT ou il en etait.
Le but : le trader arrive au payout. Non negociable.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


STATE_DIR = Path(__file__).parent.parent / "data" / "risk_desk"


@dataclass
class TradeRecord:
    """Un trade enregistre."""
    timestamp: str
    direction: str
    entry: float
    exit_price: float
    contracts: int
    pnl: float
    fees: float = 0.0
    reason: str = ""
    strategy: str = ""
    circuit_level: str = "GREEN"
    vol_regime: str = "normal"


@dataclass
class DaySummary:
    """Resume d'une journee."""
    date: str
    pnl: float
    trades: int
    wins: int
    losses: int
    max_drawdown_intraday: float = 0.0
    start_balance: float = 0.0
    end_balance: float = 0.0
    peak_balance: float = 0.0
    circuit_breakers_hit: int = 0
    blocked_count: int = 0


@dataclass
class AccountState:
    """
    Etat complet du compte — persiste entre les sessions.
    C'est la memoire du Risk Desk.
    """
    # ── Identification ──
    trader_id: str = ""
    firm: str = ""
    plan: str = ""
    instrument: str = ""
    account_size: float = 0.0

    # ── Balance tracking ──
    current_balance: float = 0.0
    peak_balance: float = 0.0           # Plus haut atteint
    initial_balance: float = 0.0         # Balance au debut

    # ── Drawdown ──
    trailing_drawdown: float = 0.0       # DD depuis le peak (negatif)
    max_trailing_dd_ever: float = 0.0    # Pire DD jamais atteint
    trailing_dd_limit: float = 0.0       # Limite prop firm

    # ── Payout tracking ──
    profit_target: float = 0.0           # Objectif prop firm
    total_profit: float = 0.0            # Profit cumule depuis le debut
    payout_progress_pct: float = 0.0     # % de progression vers le payout
    days_traded: int = 0                 # Jours de trading
    min_trading_days: int = 0            # Minimum requis

    # ── Journee en cours ──
    today_date: str = ""
    today_pnl: float = 0.0
    today_trades: int = 0
    today_wins: int = 0
    today_losses: int = 0
    today_consec_losses: int = 0
    today_peak_pnl: float = 0.0         # Plus haut PnL intraday
    today_max_dd: float = 0.0           # Pire DD intraday

    # ── Trades du jour ──
    today_trade_log: List[dict] = field(default_factory=list)

    # ── Historique par jour ──
    daily_history: List[dict] = field(default_factory=list)

    # ── Stats globales ──
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    best_day_pnl: float = 0.0
    worst_day_pnl: float = 0.0
    current_streak: int = 0              # Positif = wins, negatif = losses
    best_streak: int = 0
    worst_streak: int = 0

    # ── Meta ──
    created_at: str = ""
    last_updated: str = ""
    version: int = 1


class RiskDeskStateManager:
    """
    Gere la persistence du Risk Desk.
    Sauvegarde apres chaque trade, chaque evenement important.
    Charge au demarrage.
    """

    def __init__(self, trader_id: str = "default"):
        self.trader_id = trader_id
        self.state_dir = STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / f"state_{trader_id}.json"
        self.trades_file = self.state_dir / f"trades_{trader_id}.json"
        self.state = AccountState()
        self._all_trades: List[dict] = []

    # ── Init ──

    def initialize(self, trader_id: str, firm: str, plan: str,
                   instrument: str, account_size: float,
                   trailing_dd_limit: float, profit_target: float = 0,
                   min_trading_days: int = 0):
        """
        Initialise ou charge l'etat.
        Si un etat existe pour ce trader, on le charge.
        Sinon, on cree un nouvel etat.
        """
        # Essayer de charger
        loaded = self._load()
        if loaded and self.state.trader_id == trader_id:
            logger.info(
                f"Etat charge: {trader_id} | "
                f"Balance: ${self.state.current_balance:,.0f} | "
                f"Peak: ${self.state.peak_balance:,.0f} | "
                f"Profit: ${self.state.total_profit:+,.0f} | "
                f"Payout: {self.state.payout_progress_pct:.1f}% | "
                f"Jours: {self.state.days_traded}"
            )
            # Mise a jour des limites (au cas ou elles changent)
            self.state.trailing_dd_limit = trailing_dd_limit
            self.state.profit_target = profit_target or self.state.profit_target
            self.state.min_trading_days = min_trading_days
            self._check_new_day()
            return

        # Nouvel etat
        self.state = AccountState(
            trader_id=trader_id,
            firm=firm,
            plan=plan,
            instrument=instrument,
            account_size=account_size,
            current_balance=account_size,
            peak_balance=account_size,
            initial_balance=account_size,
            trailing_dd_limit=trailing_dd_limit,
            profit_target=profit_target,
            min_trading_days=min_trading_days,
            created_at=datetime.now().isoformat(),
            today_date=str(date.today()),
        )
        self._save()
        logger.info(
            f"Nouvel etat cree: {trader_id} | "
            f"${account_size:,.0f} | {firm} {plan} | {instrument}"
        )

    # ── Record Trade ──

    def record_trade(self, pnl: float, direction: str = "",
                     entry: float = 0, exit_price: float = 0,
                     contracts: int = 1, reason: str = "",
                     strategy: str = "", fees: float = 0,
                     circuit_level: str = "GREEN",
                     vol_regime: str = "normal"):
        """
        Enregistre un trade. Met a jour TOUT :
        - Balance, peak, drawdown
        - PnL jour, stats
        - Progression payout
        - Sauvegarde immediate
        """
        self._check_new_day()
        net_pnl = pnl + fees  # fees est negatif

        # ── Balance ──
        self.state.current_balance += net_pnl
        self.state.total_profit += net_pnl

        if self.state.current_balance > self.state.peak_balance:
            self.state.peak_balance = self.state.current_balance

        # ── Drawdown ──
        dd = self.state.current_balance - self.state.peak_balance
        self.state.trailing_drawdown = dd
        if dd < self.state.max_trailing_dd_ever:
            self.state.max_trailing_dd_ever = dd

        # ── Jour ──
        self.state.today_pnl += net_pnl
        self.state.today_trades += 1
        self.state.total_trades += 1

        if net_pnl > 0:
            self.state.today_wins += 1
            self.state.total_wins += 1
            self.state.today_consec_losses = 0
            self.state.current_streak = max(0, self.state.current_streak) + 1
        elif net_pnl < 0:
            self.state.today_losses += 1
            self.state.total_losses += 1
            self.state.today_consec_losses += 1
            self.state.current_streak = min(0, self.state.current_streak) - 1

        self.state.best_streak = max(self.state.best_streak, self.state.current_streak)
        self.state.worst_streak = min(self.state.worst_streak, self.state.current_streak)

        # Intraday peak/dd
        if self.state.today_pnl > self.state.today_peak_pnl:
            self.state.today_peak_pnl = self.state.today_pnl
        intraday_dd = self.state.today_pnl - self.state.today_peak_pnl
        if intraday_dd < self.state.today_max_dd:
            self.state.today_max_dd = intraday_dd

        # ── Payout ──
        if self.state.profit_target and self.state.profit_target > 0:
            self.state.payout_progress_pct = min(100.0, max(0.0,
                (self.state.total_profit / self.state.profit_target) * 100
            ))

        # ── Trade log ──
        trade = {
            "timestamp": datetime.now().isoformat(),
            "direction": direction,
            "entry": entry,
            "exit": exit_price,
            "contracts": contracts,
            "pnl": round(net_pnl, 2),
            "fees": round(fees, 2),
            "reason": reason,
            "strategy": strategy,
            "circuit_level": circuit_level,
            "vol_regime": vol_regime,
            "balance_after": round(self.state.current_balance, 2),
            "daily_pnl_after": round(self.state.today_pnl, 2),
            "trailing_dd": round(dd, 2),
            "payout_pct": round(self.state.payout_progress_pct, 1),
        }
        self.state.today_trade_log.append(trade)
        self._all_trades.append(trade)

        # ── Sauvegarde immediate ──
        self._save()
        self._save_trades()

        logger.info(
            f"STATE | Trade #{self.state.total_trades} | "
            f"PnL: ${net_pnl:+,.0f} | "
            f"Jour: ${self.state.today_pnl:+,.0f} | "
            f"Balance: ${self.state.current_balance:,.0f} | "
            f"DD: ${dd:,.0f} | "
            f"Payout: {self.state.payout_progress_pct:.1f}%"
        )

    # ── Sync from broker ──

    def sync_balance(self, broker_balance: float):
        """
        Synchronise la balance avec le broker.
        Appeler periodiquement pour rattraper les ecarts.
        """
        if abs(broker_balance - self.state.current_balance) > 0.01:
            old = self.state.current_balance
            # Mettre a jour total_profit pour rester en sync
            delta = broker_balance - self.state.current_balance
            self.state.total_profit += delta
            self.state.current_balance = broker_balance
            if broker_balance > self.state.peak_balance:
                self.state.peak_balance = broker_balance
            self.state.trailing_drawdown = broker_balance - self.state.peak_balance
            self._save()
            logger.info(
                f"Balance synced: ${old:,.0f} -> ${broker_balance:,.0f} "
                f"(total_profit ajuste: ${self.state.total_profit:+,.0f})"
            )

    # ── Day management ──

    def _check_new_day(self):
        """Detecte un nouveau jour et archive l'ancien."""
        today_str = str(date.today())
        if self.state.today_date == today_str:
            return

        # Archive le jour precedent
        if self.state.today_date and (self.state.today_trades > 0 or self.state.today_pnl != 0):
            summary = {
                "date": self.state.today_date,
                "pnl": round(self.state.today_pnl, 2),
                "trades": self.state.today_trades,
                "wins": self.state.today_wins,
                "losses": self.state.today_losses,
                "max_dd_intraday": round(self.state.today_max_dd, 2),
                "end_balance": round(self.state.current_balance, 2),
                "peak_balance": round(self.state.peak_balance, 2),
            }
            self.state.daily_history.append(summary)
            self.state.days_traded += 1

            # Best/worst day
            if self.state.today_pnl > self.state.best_day_pnl:
                self.state.best_day_pnl = self.state.today_pnl
            if self.state.today_pnl < self.state.worst_day_pnl:
                self.state.worst_day_pnl = self.state.today_pnl

            logger.info(
                f"Jour archive: {self.state.today_date} | "
                f"PnL: ${self.state.today_pnl:+,.0f} | "
                f"Trades: {self.state.today_trades} | "
                f"Jours: {self.state.days_traded}"
            )

        # Reset jour
        self.state.today_date = today_str
        self.state.today_pnl = 0.0
        self.state.today_trades = 0
        self.state.today_wins = 0
        self.state.today_losses = 0
        self.state.today_consec_losses = 0
        self.state.today_peak_pnl = 0.0
        self.state.today_max_dd = 0.0
        self.state.today_trade_log = []
        self._save()

    # ── Getters ──

    def get_payout_status(self) -> dict:
        """Progression vers le payout."""
        remaining = max(0, (self.state.profit_target or 0) - self.state.total_profit)
        dd_remaining = abs(self.state.trailing_dd_limit) - abs(self.state.trailing_drawdown)
        days_remaining = max(0, self.state.min_trading_days - self.state.days_traded)

        return {
            "profit_target": self.state.profit_target,
            "total_profit": round(self.state.total_profit, 2),
            "remaining": round(remaining, 2),
            "progress_pct": round(self.state.payout_progress_pct, 1),
            "days_traded": self.state.days_traded,
            "min_days_required": self.state.min_trading_days,
            "days_remaining": days_remaining,
            "trailing_dd": round(self.state.trailing_drawdown, 2),
            "trailing_dd_limit": self.state.trailing_dd_limit,
            "dd_cushion": round(dd_remaining, 2),
            "dd_used_pct": round(
                abs(self.state.trailing_drawdown) / abs(self.state.trailing_dd_limit) * 100
                if self.state.trailing_dd_limit else 0, 1
            ),
            "peak_balance": round(self.state.peak_balance, 2),
            "current_balance": round(self.state.current_balance, 2),
        }

    def get_stats(self) -> dict:
        """Stats globales."""
        win_rate = (
            self.state.total_wins / self.state.total_trades * 100
            if self.state.total_trades > 0 else 0
        )
        return {
            "total_trades": self.state.total_trades,
            "total_wins": self.state.total_wins,
            "total_losses": self.state.total_losses,
            "win_rate": round(win_rate, 1),
            "total_profit": round(self.state.total_profit, 2),
            "best_day": round(self.state.best_day_pnl, 2),
            "worst_day": round(self.state.worst_day_pnl, 2),
            "best_streak": self.state.best_streak,
            "worst_streak": self.state.worst_streak,
            "max_dd_ever": round(self.state.max_trailing_dd_ever, 2),
            "days_traded": self.state.days_traded,
        }

    def get_daily_history(self) -> List[dict]:
        """Historique jour par jour."""
        return self.state.daily_history

    def get_today(self) -> dict:
        """Etat du jour."""
        self._check_new_day()
        return {
            "date": self.state.today_date,
            "pnl": round(self.state.today_pnl, 2),
            "trades": self.state.today_trades,
            "wins": self.state.today_wins,
            "losses": self.state.today_losses,
            "consec_losses": self.state.today_consec_losses,
            "peak_pnl": round(self.state.today_peak_pnl, 2),
            "max_dd": round(self.state.today_max_dd, 2),
            "trade_log": self.state.today_trade_log,
        }

    def get_full_state(self) -> dict:
        """Etat complet (pour debug/admin)."""
        return {
            k: v for k, v in asdict(self.state).items()
            if k != "today_trade_log" and k != "daily_history"
        }

    # ── Persistence ──

    def _save(self):
        """Sauvegarde l'etat dans un fichier JSON (ecriture atomique)."""
        self.state.last_updated = datetime.now().isoformat()
        try:
            data = asdict(self.state)
            content = json.dumps(data, ensure_ascii=False, indent=2)
            # Ecriture atomique : tmp + rename pour eviter la corruption
            tmp = self.state_file.with_suffix('.tmp')
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(self.state_file)
        except Exception as e:
            logger.error(f"Erreur sauvegarde state: {e}")

    def _save_trades(self):
        """Sauvegarde tous les trades dans un fichier separe (ecriture atomique)."""
        try:
            content = json.dumps(self._all_trades, ensure_ascii=False, indent=2)
            tmp = self.trades_file.with_suffix('.tmp')
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(self.trades_file)
        except Exception as e:
            logger.error(f"Erreur sauvegarde trades: {e}")

    def _load(self) -> bool:
        """Charge l'etat depuis le fichier."""
        if not self.state_file.exists():
            return False
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            # Recree le state avec les champs existants
            self.state = AccountState(**{
                k: v for k, v in data.items()
                if k in AccountState.__dataclass_fields__
            })
            # Charge les trades
            if self.trades_file.exists():
                self._all_trades = json.loads(
                    self.trades_file.read_text(encoding="utf-8")
                )
            return True
        except Exception as e:
            logger.warning(f"Erreur chargement state: {e}")
            return False

    def reset(self, confirm: str = ""):
        """
        Reset complet de l'etat.
        Necessite confirm="RESET" pour eviter les erreurs.
        """
        if confirm != "RESET":
            logger.warning("Reset refuse — passer confirm='RESET'")
            return
        self.state = AccountState()
        self._all_trades = []
        self._save()
        self._save_trades()
        logger.warning("STATE RESET COMPLET")
