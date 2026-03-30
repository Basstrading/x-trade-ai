"""
Institutional Risk Manager — Trading Desk Grade
================================================
Un vrai risk manager comme dans les salles de marché.

Features:
- Instrument-aware (ES, NQ, MNQ, MES, GC, CL, etc.)
- Volatility-based dynamic position sizing (ATR + regime)
- Kelly Criterion fractionnel calibré sur les stats réelles
- Circuit breakers 4 niveaux (alert → reduce → halt → kill)
- Kill switch automatique via TopstepX API
- VaR / CVaR / Expected Shortfall
- Drawdown manager avec réduction progressive
- Session guard (overnight, news, heures creuses)
- Compatible Topstep rules (non-négociable)

Usage:
    rm = InstitutionalRiskManager(
        account_size=50_000,
        instrument="MNQ",
        topstep_type="50k",
    )
    rm.feed_price(price, volume, timestamp)  # flux temps réel
    size = rm.calculate_position_size(entry, stop)
    rm.can_open_trade()  # vérifie tout
    await rm.kill_all_positions(client, account_id)  # coupe tout
"""

import asyncio
import math
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta, timezone
from enum import Enum, IntEnum
from typing import Optional, Dict, List, Tuple
from loguru import logger


# ═══════════════════════════════════════════════════════════════════
# INSTRUMENT SPECIFICATIONS — Futures Contract Database
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class InstrumentSpec:
    """Spécifications d'un contrat futures."""
    symbol: str
    name: str
    exchange: str
    tick_size: float          # Mouvement minimum
    tick_value: float         # Valeur en $ d'un tick
    point_value: float        # Valeur en $ d'un point entier
    margin_day: float         # Marge intraday typique
    margin_overnight: float   # Marge overnight
    typical_atr_daily: float  # ATR journalier moyen (points)
    typical_atr_5min: float   # ATR 5-min moyen (points)
    trading_hours: str        # Heures CME (ET)
    asset_class: str          # equity_index, commodity, metal, energy, currency


# Base de données complète des contrats futures
INSTRUMENTS: Dict[str, InstrumentSpec] = {
    # ── Equity Index Futures ──
    "ES": InstrumentSpec(
        symbol="ES", name="E-mini S&P 500", exchange="CME",
        tick_size=0.25, tick_value=12.50, point_value=50.0,
        margin_day=500, margin_overnight=12_650,
        typical_atr_daily=55, typical_atr_5min=5.0,
        trading_hours="18:00-17:00 ET", asset_class="equity_index",
    ),
    "MES": InstrumentSpec(
        symbol="MES", name="Micro E-mini S&P 500", exchange="CME",
        tick_size=0.25, tick_value=1.25, point_value=5.0,
        margin_day=50, margin_overnight=1_265,
        typical_atr_daily=55, typical_atr_5min=5.0,
        trading_hours="18:00-17:00 ET", asset_class="equity_index",
    ),
    "NQ": InstrumentSpec(
        symbol="NQ", name="E-mini Nasdaq 100", exchange="CME",
        tick_size=0.25, tick_value=5.00, point_value=20.0,
        margin_day=1_000, margin_overnight=18_700,
        typical_atr_daily=250, typical_atr_5min=25.0,
        trading_hours="18:00-17:00 ET", asset_class="equity_index",
    ),
    "MNQ": InstrumentSpec(
        symbol="MNQ", name="Micro E-mini Nasdaq 100", exchange="CME",
        tick_size=0.25, tick_value=0.50, point_value=2.0,
        margin_day=100, margin_overnight=1_870,
        typical_atr_daily=250, typical_atr_5min=25.0,
        trading_hours="18:00-17:00 ET", asset_class="equity_index",
    ),
    "YM": InstrumentSpec(
        symbol="YM", name="E-mini Dow", exchange="CBOT",
        tick_size=1.0, tick_value=5.00, point_value=5.0,
        margin_day=500, margin_overnight=9_900,
        typical_atr_daily=350, typical_atr_5min=35.0,
        trading_hours="18:00-17:00 ET", asset_class="equity_index",
    ),
    "RTY": InstrumentSpec(
        symbol="RTY", name="E-mini Russell 2000", exchange="CME",
        tick_size=0.10, tick_value=5.00, point_value=50.0,
        margin_day=500, margin_overnight=7_150,
        typical_atr_daily=25, typical_atr_5min=2.5,
        trading_hours="18:00-17:00 ET", asset_class="equity_index",
    ),
    # ── Metals ──
    "GC": InstrumentSpec(
        symbol="GC", name="Gold Futures", exchange="COMEX",
        tick_size=0.10, tick_value=10.00, point_value=100.0,
        margin_day=1_000, margin_overnight=11_000,
        typical_atr_daily=30, typical_atr_5min=3.0,
        trading_hours="18:00-17:00 ET", asset_class="metal",
    ),
    "MGC": InstrumentSpec(
        symbol="MGC", name="Micro Gold", exchange="COMEX",
        tick_size=0.10, tick_value=1.00, point_value=10.0,
        margin_day=100, margin_overnight=1_100,
        typical_atr_daily=30, typical_atr_5min=3.0,
        trading_hours="18:00-17:00 ET", asset_class="metal",
    ),
    "SI": InstrumentSpec(
        symbol="SI", name="Silver Futures", exchange="COMEX",
        tick_size=0.005, tick_value=25.00, point_value=5000.0,
        margin_day=1_500, margin_overnight=15_400,
        typical_atr_daily=0.50, typical_atr_5min=0.05,
        trading_hours="18:00-17:00 ET", asset_class="metal",
    ),
    # ── Energy ──
    "CL": InstrumentSpec(
        symbol="CL", name="Crude Oil", exchange="NYMEX",
        tick_size=0.01, tick_value=10.00, point_value=1000.0,
        margin_day=1_000, margin_overnight=6_600,
        typical_atr_daily=2.0, typical_atr_5min=0.20,
        trading_hours="18:00-17:00 ET", asset_class="energy",
    ),
    "MCL": InstrumentSpec(
        symbol="MCL", name="Micro Crude Oil", exchange="NYMEX",
        tick_size=0.01, tick_value=1.00, point_value=100.0,
        margin_day=100, margin_overnight=660,
        typical_atr_daily=2.0, typical_atr_5min=0.20,
        trading_hours="18:00-17:00 ET", asset_class="energy",
    ),
    "NG": InstrumentSpec(
        symbol="NG", name="Natural Gas", exchange="NYMEX",
        tick_size=0.001, tick_value=10.00, point_value=10000.0,
        margin_day=1_000, margin_overnight=3_300,
        typical_atr_daily=0.15, typical_atr_5min=0.015,
        trading_hours="18:00-17:00 ET", asset_class="energy",
    ),
    # ── Currencies ──
    "6E": InstrumentSpec(
        symbol="6E", name="Euro FX", exchange="CME",
        tick_size=0.00005, tick_value=6.25, point_value=125000.0,
        margin_day=500, margin_overnight=2_600,
        typical_atr_daily=0.0080, typical_atr_5min=0.0008,
        trading_hours="18:00-17:00 ET", asset_class="currency",
    ),
    # ── Bonds ──
    "ZB": InstrumentSpec(
        symbol="ZB", name="30-Year T-Bond", exchange="CBOT",
        tick_size=1/32, tick_value=31.25, point_value=1000.0,
        margin_day=800, margin_overnight=4_400,
        typical_atr_daily=1.5, typical_atr_5min=0.15,
        trading_hours="18:00-17:00 ET", asset_class="bond",
    ),
}


def get_instrument(symbol: str) -> InstrumentSpec:
    """Retourne les specs d'un instrument. Raise si inconnu."""
    spec = INSTRUMENTS.get(symbol.upper())
    if not spec:
        raise ValueError(
            f"Instrument inconnu: {symbol}. "
            f"Disponibles: {', '.join(sorted(INSTRUMENTS.keys()))}"
        )
    return spec


# ═══════════════════════════════════════════════════════════════════
# TOPSTEP ACCOUNT CONFIGS (repris de l'existant, enrichi)
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TopstepConfig:
    name: str
    balance: float
    daily_loss_limit: float
    trailing_drawdown: float
    max_contracts: int  # Pour les minis (ES/NQ), x10 pour micros


TOPSTEP_ACCOUNTS: Dict[str, TopstepConfig] = {
    "25k": TopstepConfig("$25K", 25_000, -1_000, -1_500, 3),
    "50k": TopstepConfig("$50K", 50_000, -1_000, -2_000, 5),
    "100k": TopstepConfig("$100K", 100_000, -3_000, -5_000, 10),
    "150k": TopstepConfig("$150K", 150_000, -4_500, -4_500, 15),
    "300k": TopstepConfig("$300K", 300_000, -7_500, -7_500, 20),
}


# ═══════════════════════════════════════════════════════════════════
# ENUMS — Volatility Regime & Circuit Breaker Levels
# ═══════════════════════════════════════════════════════════════════

class VolRegime(str, Enum):
    """Régime de volatilité basé sur ATR relatif."""
    LOW = "low"           # ATR < 60% de la moyenne → taille normale+
    NORMAL = "normal"     # ATR 60-120% → taille normale
    HIGH = "high"         # ATR 120-180% → réduction 50%
    EXTREME = "extreme"   # ATR > 180% → réduction 75% ou stop


class CircuitLevel(IntEnum):
    """Niveaux du circuit breaker — comme sur un trading desk."""
    GREEN = 0     # Tout va bien
    YELLOW = 1    # Alerte — taille réduite
    ORANGE = 2    # Danger — taille minimale, signaux A+ seulement
    RED = 3       # HALT — on coupe tout, fini pour la journée


class SessionPhase(str, Enum):
    """Phase de la session de trading."""
    PRE_MARKET = "pre_market"       # Avant 9:30 ET
    OPEN_DRIVE = "open_drive"       # 9:30-10:00 ET (volatil)
    MORNING = "morning"             # 10:00-12:00 ET (meilleur)
    LUNCH = "lunch"                 # 12:00-14:00 ET (chop)
    AFTERNOON = "afternoon"         # 14:00-15:30 ET
    CLOSE = "close"                 # 15:30-16:00 ET (volatil)
    AFTER_HOURS = "after_hours"     # 16:00-18:00 ET
    OVERNIGHT = "overnight"         # 18:00-09:30 ET


# ═══════════════════════════════════════════════════════════════════
# VOLATILITY ENGINE — ATR temps réel + régime
# ═══════════════════════════════════════════════════════════════════

class VolatilityEngine:
    """
    Calcule la volatilité en temps réel à partir du flux de prix.
    ATR sur plusieurs timeframes + détection de régime.
    """

    def __init__(self, instrument: InstrumentSpec):
        self.instrument = instrument
        # Buffers de prix pour calcul ATR
        self._bars_5min: List[dict] = []   # {high, low, close, ts}
        self._bars_1min: List[dict] = []
        self._tick_prices: List[float] = []
        # ATR courants
        self.atr_5min: float = instrument.typical_atr_5min
        self.atr_daily: float = instrument.typical_atr_daily
        self.atr_1min: float = instrument.typical_atr_5min / math.sqrt(5)
        # Régime
        self.regime: VolRegime = VolRegime.NORMAL
        self._atr_history: List[float] = []  # Pour détecter les changements

    def feed_bar(self, high: float, low: float, close: float,
                 timeframe: str = "5min"):
        """Alimente avec une bougie complète."""
        bar = {"high": high, "low": low, "close": close, "ts": datetime.now()}

        if timeframe == "5min":
            self._bars_5min.append(bar)
            if len(self._bars_5min) > 100:
                self._bars_5min = self._bars_5min[-100:]
            self._recalc_atr_5min()
        elif timeframe == "1min":
            self._bars_1min.append(bar)
            if len(self._bars_1min) > 200:
                self._bars_1min = self._bars_1min[-200:]
            self._recalc_atr_1min()

    def feed_bars_bulk(self, bars: List[dict], timeframe: str = "5min"):
        """Alimente avec un historique de bougies (initialisation)."""
        for b in bars:
            self.feed_bar(b["high"], b["low"], b["close"], timeframe)

    def _recalc_atr_5min(self):
        """Recalcule ATR 14 périodes sur 5min."""
        bars = self._bars_5min
        if len(bars) < 15:
            return
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        # ATR = EMA des True Ranges (14 périodes)
        period = min(14, len(trs))
        self.atr_5min = sum(trs[-period:]) / period

        # Estimation ATR daily ≈ ATR_5min * sqrt(78)  (78 barres de 5min/jour)
        self.atr_daily = self.atr_5min * math.sqrt(78)

        # Mise à jour régime
        self._update_regime()

    def _recalc_atr_1min(self):
        """Recalcule ATR sur 1min."""
        bars = self._bars_1min
        if len(bars) < 15:
            return
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        period = min(14, len(trs))
        self.atr_1min = sum(trs[-period:]) / period

    def _update_regime(self):
        """Détecte le régime de volatilité."""
        # Compare ATR actuel à la moyenne historique de l'instrument
        typical = self.instrument.typical_atr_5min
        if typical == 0:
            return

        ratio = self.atr_5min / typical
        self._atr_history.append(ratio)
        if len(self._atr_history) > 500:
            self._atr_history = self._atr_history[-500:]

        if ratio < 0.60:
            self.regime = VolRegime.LOW
        elif ratio < 1.20:
            self.regime = VolRegime.NORMAL
        elif ratio < 1.80:
            self.regime = VolRegime.HIGH
        else:
            self.regime = VolRegime.EXTREME

    def get_vol_multiplier(self) -> float:
        """
        Multiplicateur de taille basé sur la volatilité.
        Vol haute → taille réduite. Vol basse → taille normale.
        """
        return {
            VolRegime.LOW: 1.0,       # Pas de bonus en low vol (prudent)
            VolRegime.NORMAL: 1.0,
            VolRegime.HIGH: 0.50,      # Moitié de la taille
            VolRegime.EXTREME: 0.25,   # Quart de la taille
        }[self.regime]

    def calculate_atr_stop(self, entry: float, direction: str,
                           multiplier: float = 2.0) -> float:
        """Stop basé sur ATR — s'adapte automatiquement à la vol."""
        stop_distance = self.atr_5min * multiplier
        if direction == "long":
            return entry - stop_distance
        return entry + stop_distance

    def get_status(self) -> dict:
        return {
            "atr_1min": round(self.atr_1min, 2),
            "atr_5min": round(self.atr_5min, 2),
            "atr_daily_est": round(self.atr_daily, 2),
            "regime": self.regime.value,
            "vol_multiplier": self.get_vol_multiplier(),
            "bars_loaded_5min": len(self._bars_5min),
            "bars_loaded_1min": len(self._bars_1min),
        }


# ═══════════════════════════════════════════════════════════════════
# POSITION SIZER — Kelly + Vol-Adjusted + Topstep Limits
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StrategyStats:
    """Statistiques d'une stratégie (backtest ou live)."""
    win_rate: float = 0.674        # Défaut MM20 Pullback
    avg_win: float = 300.0         # Points moyens gagnés
    avg_loss: float = 200.0        # Points moyens perdus
    profit_factor: float = 3.72
    total_trades: int = 1330
    max_consecutive_losses: int = 5


class PositionSizer:
    """
    Calcule la taille de position optimale en combinant :
    1. Risque fixe par trade (% du compte)
    2. Kelly Criterion fractionnel
    3. Ajustement volatilité
    4. Limites Topstep hard
    """

    def __init__(self, account_size: float, instrument: InstrumentSpec,
                 topstep: TopstepConfig, vol_engine: VolatilityEngine,
                 strategy_stats: StrategyStats = None):
        self.account_size = account_size
        self.instrument = instrument
        self.topstep = topstep
        self.vol_engine = vol_engine
        self.stats = strategy_stats or StrategyStats()

        # Paramètres de sizing
        self.risk_per_trade_pct = 0.0075   # 0.75% du compte par trade (conservateur)
        self.kelly_fraction = 0.25          # Quarter-Kelly (très conservateur)
        self.max_risk_per_trade_pct = 0.02  # Jamais plus de 2%

    def kelly_optimal(self) -> float:
        """Kelly Criterion fractionnel. Retourne % du compte à risquer."""
        W = self.stats.win_rate
        R = self.stats.avg_win / self.stats.avg_loss if self.stats.avg_loss > 0 else 1.0
        full_kelly = W - (1 - W) / R
        # Fractionnel + cap
        adjusted = max(0, full_kelly * self.kelly_fraction)
        return min(adjusted, self.max_risk_per_trade_pct)

    def calculate(self, entry: float, stop: float,
                  current_balance: float = None,
                  circuit_level: 'CircuitLevel' = CircuitLevel.GREEN) -> dict:
        """
        Calcule le nombre de contrats optimal.

        Returns dict avec contracts, risk_$, risk_%, method, détails.
        """
        balance = current_balance or self.account_size
        spec = self.instrument
        vol = self.vol_engine

        # 1. Risque en points et en dollars par contrat
        stop_distance_pts = abs(entry - stop)
        if stop_distance_pts == 0:
            return {"contracts": 0, "error": "Stop = Entry"}

        risk_per_contract = stop_distance_pts * spec.point_value

        # 2. Méthode combinée : min(fixed_fractional, kelly)
        fixed_risk_pct = self.risk_per_trade_pct
        kelly_risk_pct = self.kelly_optimal()
        # Prend le plus conservateur
        risk_pct = min(fixed_risk_pct, kelly_risk_pct)

        # 3. Montant max à risquer
        max_risk_dollars = balance * risk_pct

        # 4. Nombre de contrats brut
        raw_contracts = max_risk_dollars / risk_per_contract if risk_per_contract > 0 else 0

        # 5. Ajustement volatilité
        vol_mult = vol.get_vol_multiplier()
        vol_adjusted = raw_contracts * vol_mult

        # 6. Ajustement circuit breaker
        cb_mult = {
            CircuitLevel.GREEN: 1.0,
            CircuitLevel.YELLOW: 0.50,
            CircuitLevel.ORANGE: 0.25,
            CircuitLevel.RED: 0.0,
        }[circuit_level]
        cb_adjusted = vol_adjusted * cb_mult

        # 7. Limites Topstep hard
        # Pour les micros, le max_contracts Topstep est pour les minis
        # → multiplie par 10 pour les micros
        is_micro = spec.symbol.startswith("M") and spec.symbol != "MGC"
        max_topstep = self.topstep.max_contracts * (10 if is_micro else 1)

        # 8. Plancher à 1, plafond au max Topstep
        final_contracts = max(0, min(int(cb_adjusted), max_topstep))

        # Si circuit RED → 0
        if circuit_level == CircuitLevel.RED:
            final_contracts = 0

        actual_risk = final_contracts * risk_per_contract
        actual_risk_pct = actual_risk / balance if balance > 0 else 0

        return {
            "contracts": final_contracts,
            "risk_dollars": round(actual_risk, 2),
            "risk_pct": round(actual_risk_pct * 100, 3),
            "stop_distance_pts": round(stop_distance_pts, 2),
            "risk_per_contract": round(risk_per_contract, 2),
            "method": "min(fixed_fractional, quarter_kelly) × vol × circuit_breaker",
            "details": {
                "fixed_fractional_pct": round(fixed_risk_pct * 100, 3),
                "kelly_pct": round(kelly_risk_pct * 100, 3),
                "chosen_risk_pct": round(risk_pct * 100, 3),
                "raw_contracts": round(raw_contracts, 2),
                "vol_regime": vol.regime.value,
                "vol_multiplier": vol_mult,
                "after_vol_adj": round(vol_adjusted, 2),
                "circuit_level": circuit_level.name,
                "circuit_multiplier": cb_mult,
                "after_circuit_adj": round(cb_adjusted, 2),
                "max_topstep": max_topstep,
                "balance_used": round(balance, 2),
            },
        }


# ═══════════════════════════════════════════════════════════════════
# RISK METRICS ENGINE — VaR, CVaR, Sharpe, Sortino, Risk of Ruin
# ═══════════════════════════════════════════════════════════════════

class RiskMetrics:
    """Calcul de métriques de risque institutionnelles."""

    def __init__(self):
        self._returns: List[float] = []  # P&L en $ par trade

    def add_trade(self, pnl: float):
        self._returns.append(pnl)

    def load_history(self, pnl_list: List[float]):
        self._returns = list(pnl_list)

    def var_95(self) -> float:
        """Value at Risk 95% — perte max dans 95% des cas."""
        if len(self._returns) < 20:
            return 0.0
        return float(np.percentile(self._returns, 5))

    def var_99(self) -> float:
        """Value at Risk 99%."""
        if len(self._returns) < 20:
            return 0.0
        return float(np.percentile(self._returns, 1))

    def cvar_95(self) -> float:
        """Expected Shortfall — perte moyenne dans les 5% pires cas."""
        if len(self._returns) < 20:
            return 0.0
        arr = np.array(self._returns)
        threshold = np.percentile(arr, 5)
        tail = arr[arr <= threshold]
        return float(tail.mean()) if len(tail) > 0 else 0.0

    def sharpe_ratio(self, risk_free_daily: float = 0.0) -> float:
        """Sharpe ratio annualisé."""
        if len(self._returns) < 10:
            return 0.0
        arr = np.array(self._returns)
        excess = arr - risk_free_daily
        if excess.std() == 0:
            return 0.0
        return float((excess.mean() / excess.std()) * math.sqrt(252))

    def sortino_ratio(self) -> float:
        """Sortino — ne pénalise que la volatilité baissière."""
        if len(self._returns) < 10:
            return 0.0
        arr = np.array(self._returns)
        downside = arr[arr < 0]
        if len(downside) == 0 or downside.std() == 0:
            return float("inf") if arr.mean() > 0 else 0.0
        return float((arr.mean() / downside.std()) * math.sqrt(252))

    def max_drawdown(self) -> float:
        """Max drawdown en $ sur l'historique."""
        if not self._returns:
            return 0.0
        cumulative = np.cumsum(self._returns)
        peak = np.maximum.accumulate(cumulative)
        dd = cumulative - peak
        return float(dd.min())

    def risk_of_ruin(self, account_size: float, risk_per_trade: float) -> float:
        """
        Probabilité de ruine avec sizing actuel.
        Formule simplifiée : ((1-edge)/(1+edge))^units
        """
        if len(self._returns) < 20:
            return 0.0
        arr = np.array(self._returns)
        win_rate = (arr > 0).mean()
        if win_rate == 0 or win_rate == 1:
            return 0.0 if win_rate == 1 else 1.0
        edge = 2 * win_rate - 1  # Simplifié pour R:R ~ 1
        if edge <= 0:
            return 1.0  # Pas d'edge = ruine certaine
        units = account_size / risk_per_trade if risk_per_trade > 0 else float("inf")
        ror = ((1 - edge) / (1 + edge)) ** units
        return min(1.0, float(ror))

    def get_all(self, account_size: float = 50_000,
                risk_per_trade: float = 500) -> dict:
        n = len(self._returns)
        return {
            "total_trades": n,
            "var_95": round(self.var_95(), 2),
            "var_99": round(self.var_99(), 2),
            "cvar_95": round(self.cvar_95(), 2),
            "sharpe": round(self.sharpe_ratio(), 2),
            "sortino": round(self.sortino_ratio(), 2),
            "max_drawdown": round(self.max_drawdown(), 2),
            "risk_of_ruin": round(self.risk_of_ruin(account_size, risk_per_trade), 6),
            "avg_pnl": round(float(np.mean(self._returns)), 2) if n > 0 else 0,
            "win_rate": round(float((np.array(self._returns) > 0).mean()), 3) if n > 0 else 0,
        }


# ═══════════════════════════════════════════════════════════════════
# SESSION GUARD — Contrôle des heures de trading
# ═══════════════════════════════════════════════════════════════════

class SessionGuard:
    """
    Contrôle les phases de session.
    Empêche le trading pendant les heures dangereuses.
    """

    # Heures en ET (Eastern Time)
    # News majeures connues (jours fixes)
    FOMC_TIMES = [time(14, 0)]    # 14:00 ET typiquement
    NFP_TIME = time(8, 30)         # Premier vendredi du mois
    CPI_TIME = time(8, 30)         # ~13 du mois

    def __init__(self, allowed_phases: List[SessionPhase] = None):
        self.allowed_phases = allowed_phases or [
            SessionPhase.MORNING,
            SessionPhase.AFTERNOON,
        ]
        self.blocked_windows: List[Tuple[time, time, str]] = []
        # Bloque 5 min avant/après l'ouverture (trop volatil pour du sizing régulier)
        self.block_open_minutes = 5
        # Pas de nouvelles positions après 15:45 ET
        self.last_entry_time = time(15, 45)

    def get_phase(self, now_et: time = None) -> SessionPhase:
        """Détermine la phase de session courante (heure ET)."""
        if now_et is None:
            # Approximation : UTC-4 (EDT) ou UTC-5 (EST)
            utc_now = datetime.now(timezone.utc)
            # Simplifié : on considère EDT (mars-nov)
            et_now = utc_now - timedelta(hours=4)
            now_et = et_now.time()

        h, m = now_et.hour, now_et.minute
        t = h * 60 + m

        if t < 570:                  # < 9:30
            return SessionPhase.PRE_MARKET
        elif t < 600:                # 9:30-10:00
            return SessionPhase.OPEN_DRIVE
        elif t < 720:                # 10:00-12:00
            return SessionPhase.MORNING
        elif t < 840:                # 12:00-14:00
            return SessionPhase.LUNCH
        elif t < 930:                # 14:00-15:30
            return SessionPhase.AFTERNOON
        elif t < 960:                # 15:30-16:00
            return SessionPhase.CLOSE
        elif t < 1080:               # 16:00-18:00
            return SessionPhase.AFTER_HOURS
        else:                        # 18:00+
            return SessionPhase.OVERNIGHT

    def can_trade(self, now_et: time = None) -> Tuple[bool, str]:
        """Vérifie si la session permet le trading."""
        phase = self.get_phase(now_et)

        if phase not in self.allowed_phases:
            return False, f"Session {phase.value} — trading interdit"

        if now_et and now_et > self.last_entry_time:
            return False, f"Après {self.last_entry_time} — plus de nouvelles positions"

        # Vérifier les fenêtres bloquées (news)
        for start, end, reason in self.blocked_windows:
            if now_et and start <= now_et <= end:
                return False, f"Fenêtre bloquée: {reason}"

        return True, f"Session {phase.value} — OK"

    def add_news_block(self, event_time: time, minutes_before: int = 15,
                       minutes_after: int = 15, reason: str = "News event"):
        """Bloque le trading autour d'un événement."""
        dt_base = datetime.combine(date.today(), event_time)
        start = (dt_base - timedelta(minutes=minutes_before)).time()
        end = (dt_base + timedelta(minutes=minutes_after)).time()
        self.blocked_windows.append((start, end, reason))
        logger.info(f"🛡️ News block: {reason} [{start}-{end}]")


# ═══════════════════════════════════════════════════════════════════
# INSTITUTIONAL RISK MANAGER — Le chef d'orchestre
# ═══════════════════════════════════════════════════════════════════

class InstitutionalRiskManager:
    """
    Risk Manager Institutionnel.

    Branche-le sur TopstepX, donne-lui la taille du compte,
    et il gère TOUT le risque automatiquement :
    - Position sizing dynamique (vol + Kelly + limites)
    - Circuit breakers 4 niveaux
    - Kill switch automatique (coupe les positions)
    - Métriques VaR/CVaR temps réel
    - Session guard (heures, news)
    - Compatible 100% avec les règles Topstep
    """

    def __init__(
        self,
        account_size: float,
        instrument: str = "MNQ",
        topstep_type: str = "50k",
        strategy_stats: StrategyStats = None,
        # Circuit breaker thresholds (% de la daily loss limit Topstep)
        cb_yellow_pct: float = 0.30,   # 30% de la limite → alerte
        cb_orange_pct: float = 0.50,   # 50% → danger
        cb_red_pct: float = 0.75,      # 75% → on coupe tout
        # Max trades
        max_trades_per_day: int = 6,
        max_consecutive_losses: int = 3,
        # Session control
        allowed_sessions: List[SessionPhase] = None,
    ):
        # ── Instrument & Account ──
        self.instrument = get_instrument(instrument)
        self.topstep = TOPSTEP_ACCOUNTS.get(topstep_type.lower())
        if not self.topstep:
            raise ValueError(f"Topstep type inconnu: {topstep_type}")

        self.account_size = account_size
        self.current_balance = account_size
        self.peak_balance = account_size

        # ── Sub-engines ──
        self.vol_engine = VolatilityEngine(self.instrument)
        self.sizer = PositionSizer(
            account_size, self.instrument, self.topstep,
            self.vol_engine, strategy_stats,
        )
        self.metrics = RiskMetrics()
        self.session = SessionGuard(
            allowed_sessions or [SessionPhase.MORNING, SessionPhase.AFTERNOON]
        )

        # ── Circuit Breaker Thresholds ──
        daily_limit = abs(self.topstep.daily_loss_limit)
        self.cb_thresholds = {
            CircuitLevel.YELLOW: -daily_limit * cb_yellow_pct,
            CircuitLevel.ORANGE: -daily_limit * cb_orange_pct,
            CircuitLevel.RED: -daily_limit * cb_red_pct,
        }
        self.circuit_level = CircuitLevel.GREEN

        # ── Daily State ──
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.daily_wins: int = 0
        self.daily_losses: int = 0
        self.consecutive_losses: int = 0
        self.max_trades_per_day = max_trades_per_day
        self.max_consecutive_losses = max_consecutive_losses
        self.current_date: Optional[date] = None
        self.trade_history: List[dict] = []

        # ── Position Tracking ──
        self.open_positions: List[dict] = []
        self.current_price: float = 0.0

        # ── Kill Switch ──
        self._kill_triggered = False

        # Log startup
        logger.info(
            f"═══ Institutional Risk Manager ═══\n"
            f"  Account: ${account_size:,.0f} ({topstep_type})\n"
            f"  Instrument: {self.instrument.symbol} "
            f"(${self.instrument.point_value}/pt)\n"
            f"  Daily Limit Topstep: ${self.topstep.daily_loss_limit:,.0f}\n"
            f"  Circuit Breakers: "
            f"Y=${self.cb_thresholds[CircuitLevel.YELLOW]:,.0f} "
            f"O=${self.cb_thresholds[CircuitLevel.ORANGE]:,.0f} "
            f"R=${self.cb_thresholds[CircuitLevel.RED]:,.0f}\n"
            f"  Max Trades/Day: {max_trades_per_day} | "
            f"Max Consec Losses: {max_consecutive_losses}"
        )

    # ── Daily Reset ──

    def new_day(self):
        """Reset quotidien automatique."""
        today = date.today()
        if self.current_date == today:
            return
        if self.current_date:
            logger.info(
                f"Fin de journée {self.current_date}: "
                f"PnL=${self.daily_pnl:+,.0f} | "
                f"Trades={self.daily_trades} | "
                f"W/L={self.daily_wins}/{self.daily_losses}"
            )
        self.current_date = today
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self.consecutive_losses = 0
        self.circuit_level = CircuitLevel.GREEN
        self._kill_triggered = False
        logger.info(f"═══ Nouvelle journée: {today} | Balance: ${self.current_balance:,.0f} ═══")

    # ── Price Feed ──

    def feed_price(self, price: float, volume: float = 0,
                   timestamp: datetime = None):
        """Alimente le prix en temps réel (chaque tick ou bougie)."""
        self.current_price = price

    def feed_bar(self, high: float, low: float, close: float,
                 timeframe: str = "5min"):
        """Alimente une bougie au volatility engine."""
        self.vol_engine.feed_bar(high, low, close, timeframe)
        self.current_price = close

    # ── Core Gate : Can We Trade? ──

    def can_open_trade(self) -> Tuple[bool, str, dict]:
        """
        Check complet avant d'ouvrir un trade.
        Retourne (bool, raison, détails).
        """
        self.new_day()
        details = {}

        # 1. Kill switch
        if self._kill_triggered:
            return False, "KILL SWITCH ACTIVE — Trading terminé", details

        # 2. Circuit breaker RED
        if self.circuit_level == CircuitLevel.RED:
            return False, "CIRCUIT BREAKER RED — Positions coupées", details

        # 3. Daily P&L vs Topstep limit (hard stop)
        if self.daily_pnl <= self.topstep.daily_loss_limit:
            return False, f"LIMITE TOPSTEP ATTEINTE (${self.daily_pnl:,.0f})", details

        # 4. Max trades
        if self.daily_trades >= self.max_trades_per_day:
            return False, (
                f"Max trades atteint ({self.daily_trades}/{self.max_trades_per_day})"
            ), details

        # 5. Consecutive losses
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, (
                f"Série de {self.consecutive_losses} pertes consécutives — "
                f"pause obligatoire"
            ), details

        # 6. Trailing drawdown Topstep
        dd = self.current_balance - self.peak_balance
        if dd <= self.topstep.trailing_drawdown:
            return False, (
                f"TRAILING DD TOPSTEP (${dd:,.0f} / ${self.topstep.trailing_drawdown:,.0f})"
            ), details

        # 7. Session guard
        session_ok, session_reason = self.session.can_trade()
        if not session_ok:
            return False, session_reason, details

        # 8. Volatility extreme check
        if self.vol_engine.regime == VolRegime.EXTREME:
            details["warning"] = "VOLATILITÉ EXTRÊME — taille réduite à 25%"

        details.update({
            "circuit_level": self.circuit_level.name,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_trades": self.daily_trades,
            "consecutive_losses": self.consecutive_losses,
            "vol_regime": self.vol_engine.regime.value,
            "session": self.session.get_phase().value,
        })

        return True, "OK", details

    # ── Position Sizing ──

    def calculate_position_size(self, entry: float, stop: float) -> dict:
        """
        Calcule la taille de position optimale.
        Prend en compte volatilité, circuit breaker, limites Topstep.
        """
        self.new_day()
        return self.sizer.calculate(
            entry=entry,
            stop=stop,
            current_balance=self.current_balance,
            circuit_level=self.circuit_level,
        )

    def get_atr_stop(self, entry: float, direction: str,
                     multiplier: float = 2.0) -> dict:
        """Calcule un stop basé sur ATR."""
        stop = self.vol_engine.calculate_atr_stop(entry, direction, multiplier)
        distance = abs(entry - stop)
        risk_1ct = distance * self.instrument.point_value
        return {
            "stop_price": round(stop, 2),
            "distance_pts": round(distance, 2),
            "risk_per_contract": round(risk_1ct, 2),
            "atr_5min": round(self.vol_engine.atr_5min, 2),
            "multiplier": multiplier,
        }

    # ── Trade Recording & Circuit Breaker Updates ──

    def record_trade(self, pnl_dollars: float, direction: str = "",
                     entry: float = 0, exit_price: float = 0,
                     reason: str = "", contracts: int = 1):
        """Enregistre un trade et met à jour tous les systèmes."""
        self.new_day()

        self.daily_pnl += pnl_dollars
        self.daily_trades += 1
        self.current_balance += pnl_dollars

        if pnl_dollars > 0:
            self.daily_wins += 1
            self.consecutive_losses = 0
        elif pnl_dollars < 0:
            self.daily_losses += 1
            self.consecutive_losses += 1

        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        # Métriques
        self.metrics.add_trade(pnl_dollars)

        # Historique
        self.trade_history.append({
            "timestamp": datetime.now().isoformat(),
            "direction": direction,
            "entry": entry,
            "exit": exit_price,
            "contracts": contracts,
            "pnl": round(pnl_dollars, 2),
            "reason": reason,
            "daily_pnl": round(self.daily_pnl, 2),
            "balance": round(self.current_balance, 2),
            "circuit_level": self.circuit_level.name,
            "vol_regime": self.vol_engine.regime.value,
        })

        # Update circuit breaker
        self._update_circuit_breaker()

        level_icon = {
            CircuitLevel.GREEN: "🟢",
            CircuitLevel.YELLOW: "🟡",
            CircuitLevel.ORANGE: "🟠",
            CircuitLevel.RED: "🔴",
        }[self.circuit_level]

        logger.info(
            f"{level_icon} Trade #{self.daily_trades} | "
            f"{direction} {contracts}ct | "
            f"PnL: ${pnl_dollars:+,.0f} | "
            f"Daily: ${self.daily_pnl:+,.0f} | "
            f"Balance: ${self.current_balance:,.0f} | "
            f"Circuit: {self.circuit_level.name}"
        )

        return self.circuit_level

    def _update_circuit_breaker(self):
        """Met à jour le niveau du circuit breaker."""
        old_level = self.circuit_level

        if self.daily_pnl <= self.cb_thresholds[CircuitLevel.RED]:
            self.circuit_level = CircuitLevel.RED
        elif self.daily_pnl <= self.cb_thresholds[CircuitLevel.ORANGE]:
            self.circuit_level = CircuitLevel.ORANGE
        elif self.daily_pnl <= self.cb_thresholds[CircuitLevel.YELLOW]:
            self.circuit_level = CircuitLevel.YELLOW
        else:
            self.circuit_level = CircuitLevel.GREEN

        # Aussi déclencher sur les pertes consécutives
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.circuit_level = max(self.circuit_level, CircuitLevel.ORANGE)

        if self.circuit_level != old_level:
            logger.warning(
                f"⚡ CIRCUIT BREAKER: {old_level.name} → {self.circuit_level.name} "
                f"(Daily PnL: ${self.daily_pnl:+,.0f})"
            )

        if self.circuit_level == CircuitLevel.RED:
            logger.critical(
                f"🔴 CIRCUIT BREAKER RED — HALT TRADING "
                f"(PnL: ${self.daily_pnl:+,.0f})"
            )

    # ── Kill Switch — Force Close All Positions ──

    async def kill_all_positions(self, client, account_id: int) -> dict:
        """
        KILL SWITCH — Ferme toutes les positions ouvertes immédiatement.
        Utilise l'API TopstepX pour forcer la clôture.
        """
        self._kill_triggered = True
        logger.critical("🔴🔴🔴 KILL SWITCH ACTIVATED — Closing ALL positions 🔴🔴🔴")

        results = {"closed": [], "errors": [], "kill_time": datetime.now().isoformat()}

        try:
            # 1. Récupérer toutes les positions ouvertes
            positions = await client.search_for_positions(account_id)

            if not positions:
                results["message"] = "Aucune position ouverte"
                logger.info("Kill switch: aucune position à fermer")
                return results

            # 2. Fermer chaque position
            for pos in positions:
                try:
                    contract_id = pos.get("contractId") or pos.get("contract_id")
                    if contract_id:
                        close_result = await client.close_position(
                            account_id, contract_id
                        )
                        results["closed"].append({
                            "contract_id": contract_id,
                            "result": str(close_result),
                        })
                        logger.info(f"Position fermée: contract {contract_id}")
                except Exception as e:
                    results["errors"].append({
                        "contract_id": str(pos),
                        "error": str(e),
                    })
                    logger.error(f"Erreur fermeture position: {e}")

            # 3. Annuler tous les ordres ouverts
            try:
                open_orders = await client.search_for_open_orders(account_id)
                for order in (open_orders or []):
                    order_id = order.get("orderId") or order.get("order_id")
                    if order_id:
                        await client.cancel_order(account_id, order_id)
                        logger.info(f"Ordre annulé: {order_id}")
            except Exception as e:
                results["errors"].append({"cancel_orders": str(e)})

        except Exception as e:
            results["errors"].append({"fatal": str(e)})
            logger.error(f"Kill switch erreur fatale: {e}")

        total_closed = len(results["closed"])
        total_errors = len(results["errors"])
        logger.critical(
            f"Kill switch terminé: {total_closed} positions fermées, "
            f"{total_errors} erreurs"
        )

        return results

    async def cancel_pending_orders(self, client, account_id: int) -> int:
        """
        Annule tous les ordres pending SANS toucher aux positions ouvertes.
        Retourne le nombre d'ordres annulés.
        """
        cancelled = 0
        try:
            open_orders = await client.search_for_open_orders(account_id)
            for order in (open_orders or []):
                order_id = order.get("orderId") or order.get("order_id")
                if order_id:
                    await client.cancel_order(account_id, order_id)
                    cancelled += 1
                    logger.info(f"Ordre pending annulé (risk block): {order_id}")
        except Exception as e:
            logger.error(f"Erreur annulation ordres pending: {e}")
        return cancelled

    async def check_and_kill_if_needed(self, client, account_id: int) -> bool:
        """
        Vérifie l'état et déclenche le kill switch si nécessaire.
        À appeler périodiquement (chaque tick ou chaque seconde).
        Retourne True si le kill a été déclenché.
        """
        if self._kill_triggered:
            return True

        should_kill = False
        reason = ""

        # Red circuit breaker
        if self.circuit_level == CircuitLevel.RED:
            should_kill = True
            reason = f"Circuit breaker RED (PnL: ${self.daily_pnl:+,.0f})"

        # Topstep daily limit breach
        if self.daily_pnl <= self.topstep.daily_loss_limit:
            should_kill = True
            reason = f"Topstep daily limit breach (${self.daily_pnl:+,.0f})"

        # Trailing drawdown breach
        dd = self.current_balance - self.peak_balance
        if dd <= self.topstep.trailing_drawdown:
            should_kill = True
            reason = f"Trailing DD breach (${dd:,.0f})"

        if should_kill:
            logger.critical(f"AUTO-KILL TRIGGERED: {reason}")
            await self.kill_all_positions(client, account_id)
            return True

        return False

    # ── Unrealized P&L Monitoring ──

    def update_open_pnl(self, unrealized_pnl: float):
        """
        Met à jour le P&L non réalisé.
        Vérifie si le P&L total (réalisé + non réalisé) déclenche un circuit breaker.
        """
        total_pnl = self.daily_pnl + unrealized_pnl

        # Vérifier les seuils avec le P&L TOTAL (réalisé + non réalisé)
        if total_pnl <= self.cb_thresholds[CircuitLevel.RED]:
            if self.circuit_level != CircuitLevel.RED:
                logger.critical(
                    f"🔴 P&L non réalisé déclenche RED "
                    f"(Réalisé: ${self.daily_pnl:+,.0f} + "
                    f"Non réalisé: ${unrealized_pnl:+,.0f} = "
                    f"${total_pnl:+,.0f})"
                )
                self.circuit_level = CircuitLevel.RED

    # ── Status Dashboard ──

    def get_status(self) -> dict:
        """Retourne l'état complet — pour l'API / dashboard."""
        self.new_day()
        can, reason, details = self.can_open_trade()
        dd = self.current_balance - self.peak_balance

        daily_limit = abs(self.topstep.daily_loss_limit)
        daily_used_pct = (abs(self.daily_pnl) / daily_limit * 100
                          if self.daily_pnl < 0 else 0)
        trailing_dd_pct = (abs(dd) / abs(self.topstep.trailing_drawdown) * 100
                           if dd < 0 else 0)

        return {
            # Account
            "account_size": self.account_size,
            "current_balance": round(self.current_balance, 2),
            "peak_balance": round(self.peak_balance, 2),
            "topstep_type": self.topstep.name,

            # Instrument
            "instrument": self.instrument.symbol,
            "point_value": self.instrument.point_value,
            "tick_value": self.instrument.tick_value,

            # Volatility
            "volatility": self.vol_engine.get_status(),

            # Daily State
            "can_trade": can,
            "reason": reason,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_trades": self.daily_trades,
            "daily_wins": self.daily_wins,
            "daily_losses": self.daily_losses,
            "consecutive_losses": self.consecutive_losses,

            # Limits
            "daily_limit_topstep": self.topstep.daily_loss_limit,
            "daily_used_pct": round(daily_used_pct, 1),
            "trailing_dd": round(dd, 2),
            "trailing_dd_limit": self.topstep.trailing_drawdown,
            "trailing_dd_pct": round(trailing_dd_pct, 1),

            # Circuit Breaker
            "circuit_level": self.circuit_level.name,
            "circuit_thresholds": {
                k.name: round(v, 0) for k, v in self.cb_thresholds.items()
            },
            "kill_triggered": self._kill_triggered,

            # Session
            "session_phase": self.session.get_phase().value,

            # Risk Metrics
            "risk_metrics": self.metrics.get_all(
                self.account_size,
                abs(self.topstep.daily_loss_limit) * 0.1,
            ),

            # Max position
            "max_contracts_topstep": self.topstep.max_contracts,
        }

    def get_sizing_preview(self, entry: float, stop: float) -> dict:
        """
        Preview complet du sizing pour un trade potentiel.
        Utile pour le dashboard.
        """
        sizing = self.calculate_position_size(entry, stop)
        atr_info = self.get_atr_stop(entry, "long" if stop < entry else "short")

        return {
            "sizing": sizing,
            "atr_stop": atr_info,
            "can_trade": self.can_open_trade(),
            "vol_regime": self.vol_engine.regime.value,
            "circuit_level": self.circuit_level.name,
        }

    # ── Reconfiguration ──

    def reconfigure(self, account_size: float = None, instrument: str = None,
                    topstep_type: str = None):
        """Reconfigure à chaud (ex: changement de compte ou instrument)."""
        if account_size:
            self.account_size = account_size
            self.current_balance = account_size
            self.peak_balance = account_size
            self.sizer.account_size = account_size

        if instrument:
            self.instrument = get_instrument(instrument)
            self.vol_engine = VolatilityEngine(self.instrument)
            self.sizer.instrument = self.instrument
            self.sizer.vol_engine = self.vol_engine

        if topstep_type:
            self.topstep = TOPSTEP_ACCOUNTS[topstep_type.lower()]
            self.sizer.topstep = self.topstep
            daily_limit = abs(self.topstep.daily_loss_limit)
            self.cb_thresholds = {
                CircuitLevel.YELLOW: -daily_limit * 0.30,
                CircuitLevel.ORANGE: -daily_limit * 0.50,
                CircuitLevel.RED: -daily_limit * 0.75,
            }

        logger.info(
            f"Reconfiguré: ${self.account_size:,.0f} | "
            f"{self.instrument.symbol} | {self.topstep.name}"
        )


# ═══════════════════════════════════════════════════════════════════
# FACTORY — Création rapide
# ═══════════════════════════════════════════════════════════════════

def create_risk_manager(
    account_size: float = 50_000,
    instrument: str = "MNQ",
    topstep_type: str = "50k",
    **kwargs,
) -> InstitutionalRiskManager:
    """
    Factory pour créer un risk manager configuré.

    Exemples:
        rm = create_risk_manager(50_000, "MNQ")
        rm = create_risk_manager(150_000, "ES", "150k")
        rm = create_risk_manager(50_000, "GC", "50k")
    """
    return InstitutionalRiskManager(
        account_size=account_size,
        instrument=instrument,
        topstep_type=topstep_type,
        **kwargs,
    )
