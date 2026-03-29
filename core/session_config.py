"""
Session Config — Parametres ajustables AUTO / MANUEL
=====================================================
Chaque parametre du Risk Desk peut etre en mode :
- AUTO : le systeme detecte le contexte et ajuste
- MANUEL : le risk manager override avec sa propre valeur

Le trader ne voit rien de tout ca. Il voit le resultat.
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta, timezone
from enum import Enum
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from loguru import logger


class ParamMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


# ═══════════════════════════════════════════════════════════════
# ADJUSTABLE PARAMETER — Un parametre avec toggle AUTO/MANUEL
# ═══════════════════════════════════════════════════════════════

@dataclass
class AdjustableParam:
    """
    Un parametre qui peut etre en AUTO ou MANUEL.
    - auto_value : valeur calculee par le systeme
    - manual_value : valeur imposee par le risk manager
    - mode : lequel on utilise
    """
    name: str
    mode: ParamMode = ParamMode.AUTO
    auto_value: float = 0.0
    manual_value: float = 0.0
    auto_reason: str = ""       # Pourquoi le systeme a choisi cette valeur

    @property
    def value(self) -> float:
        """La valeur effective."""
        if self.mode == ParamMode.MANUAL:
            return self.manual_value
        return self.auto_value

    def set_manual(self, value: float):
        """Passe en MANUEL avec cette valeur."""
        self.mode = ParamMode.MANUAL
        self.manual_value = value

    def set_auto(self):
        """Repasse en AUTO."""
        self.mode = ParamMode.AUTO

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode.value,
            "value": self.value,
            "auto_value": self.auto_value,
            "manual_value": self.manual_value,
            "auto_reason": self.auto_reason,
        }


# ═══════════════════════════════════════════════════════════════
# NEWS DETECTOR — Detection automatique des events macro
# ═══════════════════════════════════════════════════════════════

# Events majeurs qui impactent la volatilite
HIGH_IMPACT_KEYWORDS = [
    "FOMC", "Non-Farm", "NFP", "CPI", "PPI",
    "Fed Chair", "Powell", "GDP", "Retail Sales",
    "ISM Manufacturing", "Unemployment Rate",
    "Interest Rate Decision", "Jackson Hole",
]


class NewsDetector:
    """
    Detecte les events macro du jour.
    Charge le calendrier economique et verifie si on est un jour de news.
    """

    def __init__(self, calendar_path: str = None):
        self._events: Dict[str, List[dict]] = {}  # date_str -> [{event, time, tier}]
        self._loaded = False

        if calendar_path:
            self._load_calendar(calendar_path)
        else:
            # Essaye les fichiers par defaut
            base = Path(__file__).parent.parent / "data"
            for name in ["news_calendar_nfp_cpi.csv", "us_economic_calendar.csv"]:
                path = base / name
                if path.exists():
                    self._load_calendar(str(path))
                    break

    def _load_calendar(self, path: str):
        """Charge un calendrier CSV."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Format 1: date,time_et,events,tier
                    if "date" in row and "events" in row:
                        d = row["date"]
                        self._events.setdefault(d, []).append({
                            "event": row.get("events", ""),
                            "time": row.get("time_et", ""),
                            "tier": int(row.get("tier", 2)),
                        })
                    # Format 2: DateTime,Event,Impact,...
                    elif "DateTime" in row and "Event" in row:
                        dt_str = row["DateTime"]
                        try:
                            dt = datetime.fromisoformat(dt_str.replace("+03:30", "+00:00"))
                            d = dt.strftime("%Y-%m-%d")
                        except Exception:
                            continue
                        impact = row.get("Impact", "")
                        tier = 1 if "High" in impact else 2
                        self._events.setdefault(d, []).append({
                            "event": row["Event"],
                            "time": dt.strftime("%H:%M"),
                            "tier": tier,
                        })
            self._loaded = True
            logger.info(f"Calendrier charge: {len(self._events)} jours, source: {path}")
        except Exception as e:
            logger.warning(f"Calendrier non charge: {e}")

    def get_today_events(self, today: date = None) -> List[dict]:
        """Retourne les events du jour."""
        d = str(today or date.today())
        return self._events.get(d, [])

    def is_high_impact_day(self, today: date = None) -> Tuple[bool, List[str]]:
        """
        Est-ce un jour de news a fort impact ?
        Retourne (bool, liste des events).
        """
        events = self.get_today_events(today)
        high = []
        for ev in events:
            # Tier 1 ou keyword high impact
            name = ev.get("event", "")
            if ev.get("tier") == 1:
                high.append(name)
            elif any(kw.lower() in name.lower() for kw in HIGH_IMPACT_KEYWORDS):
                high.append(name)
        return len(high) > 0, high

    def add_manual_event(self, event_name: str, event_date: date = None,
                         event_time: str = "08:30", tier: int = 1):
        """Ajoute un event manuellement (ex: earnings d'une action)."""
        d = str(event_date or date.today())
        self._events.setdefault(d, []).append({
            "event": event_name,
            "time": event_time,
            "tier": tier,
        })


# ═══════════════════════════════════════════════════════════════
# MARKET REGIME DETECTOR — Choppy / Trend / Range
# ═══════════════════════════════════════════════════════════════

class MarketRegime(str, Enum):
    TREND = "trend"         # Mouvement directionnel clair
    RANGE = "range"         # Marche lateral
    CHOPPY = "choppy"       # Volatil sans direction (le pire)
    UNKNOWN = "unknown"


class MarketRegimeDetector:
    """
    Detecte le regime de marche a partir des bougies.
    - Trend : barres directionnelles, peu de meches
    - Range : prix oscillent entre deux bornes
    - Choppy : grosses meches, reversals frequents
    """

    def __init__(self):
        self._bars: List[dict] = []  # {open, high, low, close}
        self.regime: MarketRegime = MarketRegime.UNKNOWN
        self._choppiness: float = 50.0  # 0-100

    def feed_bar(self, open_p: float, high: float, low: float, close: float):
        """Alimente une bougie 5min."""
        self._bars.append({"o": open_p, "h": high, "l": low, "c": close})
        if len(self._bars) > 100:
            self._bars = self._bars[-100:]
        if len(self._bars) >= 14:
            self._update_regime()

    def _update_regime(self):
        """Met a jour le regime avec le Choppiness Index."""
        bars = self._bars[-14:]
        # ATR sum
        atr_sum = 0.0
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            atr_sum += tr

        # Range sur la periode
        highest = max(b["h"] for b in bars)
        lowest = min(b["l"] for b in bars)
        total_range = highest - lowest

        if total_range == 0 or atr_sum == 0:
            return

        import math
        # Choppiness Index = 100 * LOG10(ATR_sum / range) / LOG10(n)
        n = len(bars)
        ci = 100 * math.log10(atr_sum / total_range) / math.log10(n)
        self._choppiness = min(100, max(0, ci))

        # Classification
        if self._choppiness > 61.8:
            self.regime = MarketRegime.CHOPPY
        elif self._choppiness < 38.2:
            self.regime = MarketRegime.TREND
        else:
            self.regime = MarketRegime.RANGE

    def get_trade_count_multiplier(self) -> float:
        """
        Multiplicateur pour le nombre de trades.
        Choppy → moins de trades. Trend → normal.
        """
        return {
            MarketRegime.TREND: 1.0,
            MarketRegime.RANGE: 0.75,
            MarketRegime.CHOPPY: 0.50,
            MarketRegime.UNKNOWN: 1.0,
        }[self.regime]

    def get_atr_multiplier(self) -> float:
        """
        Multiplicateur ATR pour le stop.
        Choppy → stop plus large. Trend → normal.
        """
        return {
            MarketRegime.TREND: 1.0,      # Stop normal
            MarketRegime.RANGE: 1.25,     # Un peu plus large
            MarketRegime.CHOPPY: 1.5,     # Beaucoup plus large (meches)
            MarketRegime.UNKNOWN: 1.0,
        }[self.regime]


# ═══════════════════════════════════════════════════════════════
# TRADER PERFORMANCE — Detection de tilt
# ═══════════════════════════════════════════════════════════════

class TraderPerformance:
    """
    Analyse la performance intraday du trader.
    Detecte le tilt : pertes rapides, revenge trading, etc.
    """

    def __init__(self):
        self._trade_times: List[datetime] = []
        self._trade_pnls: List[float] = []
        self.tilt_detected: bool = False
        self.tilt_reason: str = ""

    def record_trade(self, pnl: float, timestamp: datetime = None):
        """Enregistre un trade."""
        ts = timestamp or datetime.now()
        self._trade_times.append(ts)
        self._trade_pnls.append(pnl)

    def check_tilt(self) -> Tuple[bool, str]:
        """
        Detecte le tilt basé sur :
        1. Frequence des trades (revenge trading = trades trop rapprochés)
        2. Serie de pertes
        3. Taille croissante des pertes
        """
        if len(self._trade_pnls) < 2:
            return False, ""

        # Check 1 : 3 trades en moins de 5 minutes → revenge trading
        if len(self._trade_times) >= 3:
            last_3 = self._trade_times[-3:]
            span = (last_3[-1] - last_3[0]).total_seconds()
            if span < 300:  # 5 minutes
                last_3_pnl = self._trade_pnls[-3:]
                losses = sum(1 for p in last_3_pnl if p < 0)
                if losses >= 2:
                    self.tilt_detected = True
                    self.tilt_reason = "Revenge trading detecte (3 trades en 5min dont 2+ pertes)"
                    return True, self.tilt_reason

        # Check 2 : Pertes croissantes (chaque perte plus grosse que la precedente)
        recent_losses = [p for p in self._trade_pnls[-5:] if p < 0]
        if len(recent_losses) >= 3:
            # Verifier si les pertes augmentent
            increasing = all(
                abs(recent_losses[i]) > abs(recent_losses[i-1])
                for i in range(1, len(recent_losses))
            )
            if increasing:
                self.tilt_detected = True
                self.tilt_reason = "Pertes croissantes — possible escalade emotionnelle"
                return True, self.tilt_reason

        self.tilt_detected = False
        self.tilt_reason = ""
        return False, ""

    def get_cb_multiplier(self) -> float:
        """
        Circuit breaker plus strict si tilt detecte.
        Retourne un multiplicateur pour les seuils (plus petit = plus strict).
        """
        if self.tilt_detected:
            return 0.60  # CB 40% plus strict
        return 1.0


# ═══════════════════════════════════════════════════════════════
# SESSION CONFIG — Le chef d'orchestre AUTO/MANUEL
# ═══════════════════════════════════════════════════════════════

class SessionConfig:
    """
    Configuration de session avec toggle AUTO/MANUEL pour chaque parametre.

    Parametres ajustables :
    1. position_size_mult  — Multiplicateur de taille (1.0 = normal)
    2. atr_stop_mult       — Multiplicateur ATR pour le stop (2.0 = defaut)
    3. max_trades          — Nombre de trades autorise
    4. cb_strictness       — Severite des circuit breakers (1.0 = normal, 0.5 = 2x plus strict)

    En mode AUTO, ces valeurs sont calculees par :
    - NewsDetector (FOMC, NFP → taille reduite)
    - MarketRegimeDetector (choppy → moins de trades, stop plus large)
    - TraderPerformance (tilt → CB plus strict)
    """

    def __init__(self, base_max_trades: int = 6, base_atr_mult: float = 2.0):
        # Les 4 parametres ajustables
        self.position_size_mult = AdjustableParam("Taille de position")
        self.atr_stop_mult = AdjustableParam("Multiplicateur stop ATR")
        self.max_trades = AdjustableParam("Trades max / jour")
        self.cb_strictness = AdjustableParam("Severite circuit breakers")

        # Valeurs de base (avant ajustements)
        self._base_max_trades = base_max_trades
        self._base_atr_mult = base_atr_mult

        # Sous-modules d'auto-detection
        self.news = NewsDetector()
        self.market = MarketRegimeDetector()
        self.trader = TraderPerformance()

        # Bloquer manuellement le trader
        self.manually_blocked: bool = False
        self.block_reason: str = ""

        # Force reduction globale ("FOMC today, tout le monde a 50%")
        self.global_reduction: Optional[float] = None
        self.global_reduction_reason: str = ""

        # Initialiser les valeurs auto
        self._recalculate()

    def _recalculate(self):
        """Recalcule toutes les valeurs AUTO."""
        # ── Taille (news impact) ──
        is_news, events = self.news.is_high_impact_day()
        if is_news:
            self.position_size_mult.auto_value = 0.50
            self.position_size_mult.auto_reason = f"News: {', '.join(events[:3])}"
        else:
            self.position_size_mult.auto_value = 1.0
            self.position_size_mult.auto_reason = "Pas de news majeure"

        # Global reduction override
        if self.global_reduction is not None:
            self.position_size_mult.auto_value = min(
                self.position_size_mult.auto_value,
                self.global_reduction,
            )
            if self.global_reduction_reason:
                self.position_size_mult.auto_reason = self.global_reduction_reason

        # ── ATR stop mult (market regime) ──
        regime_atr = self.market.get_atr_multiplier()
        self.atr_stop_mult.auto_value = self._base_atr_mult * regime_atr
        self.atr_stop_mult.auto_reason = f"Regime: {self.market.regime.value} (x{regime_atr})"

        # ── Max trades (market regime) ──
        regime_trades = self.market.get_trade_count_multiplier()
        self.max_trades.auto_value = max(1, int(self._base_max_trades * regime_trades))
        self.max_trades.auto_reason = f"Regime: {self.market.regime.value} (x{regime_trades})"

        # ── Circuit breaker strictness (trader tilt) ──
        tilt, tilt_reason = self.trader.check_tilt()
        cb_mult = self.trader.get_cb_multiplier()
        self.cb_strictness.auto_value = cb_mult
        if tilt:
            self.cb_strictness.auto_reason = tilt_reason
        else:
            self.cb_strictness.auto_reason = "Trader en forme"

    # ── Actions admin ──

    def block_trader(self, reason: str = "Bloque par le risk manager"):
        """Bloque manuellement le trader."""
        self.manually_blocked = True
        self.block_reason = reason
        logger.warning(f"TRADER BLOQUE: {reason}")

    def unblock_trader(self):
        """Debloque le trader."""
        self.manually_blocked = False
        self.block_reason = ""
        logger.info("Trader debloque")

    def set_global_reduction(self, mult: float, reason: str = ""):
        """Force une reduction globale de taille."""
        self.global_reduction = mult
        self.global_reduction_reason = reason
        self._recalculate()
        logger.info(f"Reduction globale: {mult:.0%} — {reason}")

    def clear_global_reduction(self):
        """Annule la reduction globale."""
        self.global_reduction = None
        self.global_reduction_reason = ""
        self._recalculate()

    # ── Feed ──

    def feed_bar(self, open_p: float, high: float, low: float, close: float):
        """Alimente le detecteur de regime."""
        self.market.feed_bar(open_p, high, low, close)
        self._recalculate()

    def record_trade(self, pnl: float):
        """Enregistre un trade pour la detection de tilt."""
        self.trader.record_trade(pnl)
        self._recalculate()

    # ── Getters (valeurs effectives) ──

    @property
    def effective_size_mult(self) -> float:
        return self.position_size_mult.value

    @property
    def effective_atr_mult(self) -> float:
        return self.atr_stop_mult.value

    @property
    def effective_max_trades(self) -> int:
        return int(self.max_trades.value)

    @property
    def effective_cb_strictness(self) -> float:
        return self.cb_strictness.value

    # ── Status ──

    def to_dict(self) -> dict:
        return {
            "params": {
                "position_size_mult": self.position_size_mult.to_dict(),
                "atr_stop_mult": self.atr_stop_mult.to_dict(),
                "max_trades": self.max_trades.to_dict(),
                "cb_strictness": self.cb_strictness.to_dict(),
            },
            "manually_blocked": self.manually_blocked,
            "block_reason": self.block_reason,
            "global_reduction": self.global_reduction,
            "global_reduction_reason": self.global_reduction_reason,
            "detectors": {
                "news": {
                    "is_high_impact": self.news.is_high_impact_day()[0],
                    "events": self.news.is_high_impact_day()[1],
                },
                "market_regime": self.market.regime.value,
                "choppiness": round(self.market._choppiness, 1),
                "tilt_detected": self.trader.tilt_detected,
                "tilt_reason": self.trader.tilt_reason,
            },
        }
