"""
Prop Firm Rules Database
========================
Regles NON NEGOCIABLES de chaque prop firm.
Le trader ne peut ni les voir ni les modifier.

Supported: Topstep, Apex, Bulenox, Tradeify, TakeProfitTrader (TPT)

Sources (mars 2026):
- Topstep: topstepx.com
- Apex: apextraderfunding.com
- Bulenox: bulenox.com + bulenox.com/help
- Tradeify: tradeify.co + help.tradeify.co
- TakeProfitTrader: takeprofittrader.com

IMPORTANT: Les regles changent souvent. Verifier les sites officiels.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum


class PropFirmType(str, Enum):
    TOPSTEP = "topstep"
    APEX = "apex"
    BULENOX = "bulenox"
    TRADEIFY = "tradeify"
    TPT = "tpt"             # TakeProfitTrader
    CUSTOM = "custom"


@dataclass(frozen=True)
class ScalingTier:
    """Un palier du scaling plan."""
    min_profit: float       # Profit minimum pour debloquer
    max_contracts: int      # Contrats minis autorises a ce palier


@dataclass(frozen=True)
class PropFirmAccountRules:
    """Regles d'un compte prop firm — immuables une fois creees."""
    firm: PropFirmType
    plan_name: str
    account_size: float
    daily_loss_limit: float          # Negatif (ex: -1000), 0 = pas de limite
    max_drawdown: float              # Trailing ou fixe, negatif
    trailing_drawdown: bool          # True = trailing temps reel
    eod_drawdown: bool = False       # True = drawdown calcule en fin de jour (EOD)
    dd_locks_at_breakeven: bool = False  # True = le DD stop de trailer au break-even
    max_contracts: int = 10          # Pour les minis (x10 pour micros)
    max_position_size: int = 10      # Max contrats par position
    profit_target: Optional[float] = None  # Objectif de profit
    # Regles specifiques
    no_overnight: bool = True        # Pas de position overnight
    no_weekend: bool = True          # Pas de position le weekend
    no_news_trading: bool = False    # Interdit de trader les news
    news_blackout_seconds: int = 0   # Secondes avant/apres news Tier 1 (ex: 60 = 1min)
    consistency_rule: bool = False   # Regle de consistance
    consistency_pct: float = 0.0     # Ex: 0.40 = max 40% du profit total en 1 jour
    min_trading_days: int = 0        # Jours minimum de trading
    scaling_plan: bool = False       # Taille progressive (commence petit)
    scaling_tiers: Tuple[ScalingTier, ...] = ()  # Paliers du scaling
    daily_trade_limit: Optional[int] = None  # Max trades/jour (None = illimite)
    # Payout
    payout_buffer: float = 0.0       # Cushion a maintenir avant de retirer (TPT)
    max_payout_first: float = 0.0    # Plafond premier retrait
    # Notes
    notes: str = ""                  # Pieges specifiques a connaitre


# ═══════════════════════════════════════════════════════════════
# TOPSTEP — Futures (CME) — topstepx.com
# Piege principal: trailing drawdown temps reel, pas de daily limit
# officielle sur les nouveaux comptes mais agents conservateurs
# ═══════════════════════════════════════════════════════════════

TOPSTEP_PLANS: Dict[str, PropFirmAccountRules] = {
    # Topstep n'a que 3 comptes : 50K, 100K, 150K
    # Daily loss limit SUPPRIMEE depuis aout 2024 sur TopstepX
    # Consistency rule 50% (best day < 50% du total)
    # Flat avant 3:10 PM CT (risk team flatten a 3:08 PM CT)
    "50k": PropFirmAccountRules(
        firm=PropFirmType.TOPSTEP,
        plan_name="TopstepX $50K",
        account_size=50_000,
        daily_loss_limit=0,             # Supprimee depuis aout 2024 — AUCUN filet
        max_drawdown=-2_000,            # Si tu touches ca, le compte est MORT
        trailing_drawdown=True,         # Trailing EOD (pas temps reel)
        eod_drawdown=True,              # Calcule en fin de journee
        max_contracts=5,
        max_position_size=5,
        profit_target=3_000,
        no_overnight=True,              # Flat avant 3:10 PM CT
        consistency_rule=True,
        consistency_pct=0.50,
        notes="PAS de daily loss limit = le DD est le SEUL filet. EOD trailing. "
              "Compte MORT si DD touche. Risk desk doit imposer sa propre limite jour.",
    ),
    "100k": PropFirmAccountRules(
        firm=PropFirmType.TOPSTEP,
        plan_name="TopstepX $100K",
        account_size=100_000,
        daily_loss_limit=0,
        max_drawdown=-3_000,
        trailing_drawdown=True,
        eod_drawdown=True,
        max_contracts=10,
        max_position_size=10,
        profit_target=6_000,
        no_overnight=True,
        consistency_rule=True,
        consistency_pct=0.50,
        notes="PAS de daily loss limit. EOD trailing DD. Compte mort si DD touche.",
    ),
    "150k": PropFirmAccountRules(
        firm=PropFirmType.TOPSTEP,
        plan_name="TopstepX $150K",
        account_size=150_000,
        daily_loss_limit=0,
        max_drawdown=-4_500,
        trailing_drawdown=True,
        eod_drawdown=True,
        max_contracts=15,
        max_position_size=15,
        profit_target=9_000,
        no_overnight=True,
        consistency_rule=True,
        consistency_pct=0.50,
    ),
}


# ═══════════════════════════════════════════════════════════════
# APEX — Futures (Rithmic) — apextraderfunding.com
# Piege principal: consistency rule 30%, overnight autorise
# mais gap risk compte dans le drawdown
# ═══════════════════════════════════════════════════════════════

APEX_PLANS: Dict[str, PropFirmAccountRules] = {
    # Apex 4.0 (mars 2026) — refonte majeure
    # $250K et $300K supprimes
    # Consistency passee de 30% a 50%
    # Daily loss limit = EOD seulement (pas sur intraday accounts)
    # Flat avant 4:59 PM ET
    # 6 payouts max par PA, montants escaliers
    # Metaux suspendus (Gold, Silver, etc.)
    "25k": PropFirmAccountRules(
        firm=PropFirmType.APEX,
        plan_name="Apex $25K (4.0)",
        account_size=25_000,
        daily_loss_limit=-500,          # EOD seulement
        max_drawdown=-1_000,            # Reduit de -1,500 a -1,000
        trailing_drawdown=True,
        max_contracts=4,
        max_position_size=4,
        profit_target=1_500,
        no_overnight=True,              # Change: flat avant 4:59 PM ET
        consistency_rule=True,
        consistency_pct=0.50,           # Change: 50% (etait 30%)
        notes="Apex 4.0 mars 2026. 6 payouts max par PA. Metaux suspendus.",
    ),
    "50k": PropFirmAccountRules(
        firm=PropFirmType.APEX,
        plan_name="Apex $50K (4.0)",
        account_size=50_000,
        daily_loss_limit=-1_000,        # Reduit de -1,100
        max_drawdown=-2_000,            # Reduit de -2,500
        trailing_drawdown=True,
        max_contracts=6,                # Reduit de 10
        max_position_size=6,
        profit_target=3_000,
        no_overnight=True,
        consistency_rule=True,
        consistency_pct=0.50,
    ),
    "100k": PropFirmAccountRules(
        firm=PropFirmType.APEX,
        plan_name="Apex $100K (4.0)",
        account_size=100_000,
        daily_loss_limit=-1_500,        # Reduit de -2,000
        max_drawdown=-3_000,            # Inchange
        trailing_drawdown=True,
        max_contracts=8,                # Reduit de 14
        max_position_size=8,
        profit_target=6_000,
        no_overnight=True,
        consistency_rule=True,
        consistency_pct=0.50,
        min_trading_days=5,             # 5 jours qualifiants avant payout
    ),
    "150k": PropFirmAccountRules(
        firm=PropFirmType.APEX,
        plan_name="Apex $150K (4.0)",
        account_size=150_000,
        daily_loss_limit=-2_000,        # Reduit de -3,500
        max_drawdown=-4_000,            # Reduit de -5,000
        trailing_drawdown=True,
        max_contracts=12,               # Reduit de 17
        max_position_size=12,
        profit_target=9_000,
        no_overnight=True,
        consistency_rule=True,
        consistency_pct=0.50,
        min_trading_days=5,
    ),
}


# ═══════════════════════════════════════════════════════════════
# BULENOX — Futures (CME) — bulenox.com
# 2 options par compte:
#   Option 1: Trailing DD temps reel, PAS de daily loss limit, full size
#   Option 2: EOD DD, daily loss limit, scaling progressif
# Piege principal: Option 1 le trailing est tick-by-tick (meches tuent)
# Consistency rule 40% sur Master/Funded (PAS en eval)
# ═══════════════════════════════════════════════════════════════

BULENOX_PLANS: Dict[str, PropFirmAccountRules] = {
    # ── Option 1 (Trailing, pas de daily limit, full size) ──
    "25k_opt1": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $25K Option 1 (Trailing)",
        account_size=25_000,
        daily_loss_limit=0,             # PAS de daily loss limit
        max_drawdown=-1_500,
        trailing_drawdown=True,         # Trailing temps reel tick-by-tick
        dd_locks_at_breakeven=True,     # Lock a $23,500 puis stop
        max_contracts=3,
        max_position_size=3,
        profit_target=1_500,
        no_overnight=False,             # Overnight autorise dans la session
        consistency_rule=True,          # 40% sur Master/Funded
        consistency_pct=0.40,
        min_trading_days=10,            # 10 jours avant premier retrait (Master)
        notes="Trailing tick-by-tick. Lock au break-even. Flipping rule subjective.",
    ),
    "50k_opt1": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $50K Option 1 (Trailing)",
        account_size=50_000,
        daily_loss_limit=0,
        max_drawdown=-2_500,
        trailing_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=7,
        max_position_size=7,
        profit_target=3_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        min_trading_days=10,
        notes="DD $2,500 (site officiel). Certaines sources disent $2,000 — verifier.",
    ),
    "100k_opt1": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $100K Option 1 (Trailing)",
        account_size=100_000,
        daily_loss_limit=0,
        max_drawdown=-3_000,
        trailing_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=12,
        max_position_size=12,
        profit_target=6_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        min_trading_days=10,
    ),
    "150k_opt1": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $150K Option 1 (Trailing)",
        account_size=150_000,
        daily_loss_limit=0,
        max_drawdown=-4_500,
        trailing_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=15,
        max_position_size=15,
        profit_target=9_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        min_trading_days=10,
    ),
    "250k_opt1": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $250K Option 1 (Trailing)",
        account_size=250_000,
        daily_loss_limit=0,
        max_drawdown=-5_500,
        trailing_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=25,
        max_position_size=25,
        profit_target=15_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        min_trading_days=10,
    ),

    # ── Option 2 (EOD DD, daily limit, scaling) ──
    "25k_opt2": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $25K Option 2 (EOD)",
        account_size=25_000,
        daily_loss_limit=-500,
        max_drawdown=-1_500,
        trailing_drawdown=False,
        eod_drawdown=True,
        max_contracts=3,
        max_position_size=3,
        profit_target=1_500,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 2),
            ScalingTier(1_500, 3),
        ),
        min_trading_days=10,
        notes="Scaling: 2 contrats au depart, 3 apres $1,500 profit",
    ),
    "50k_opt2": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $50K Option 2 (EOD)",
        account_size=50_000,
        daily_loss_limit=-1_100,
        max_drawdown=-2_500,
        trailing_drawdown=False,
        eod_drawdown=True,
        max_contracts=7,
        max_position_size=7,
        profit_target=3_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 2),
            ScalingTier(1_500, 4),
            ScalingTier(4_000, 7),
        ),
        min_trading_days=10,
    ),
    "100k_opt2": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $100K Option 2 (EOD)",
        account_size=100_000,
        daily_loss_limit=-2_200,
        max_drawdown=-3_000,
        trailing_drawdown=False,
        eod_drawdown=True,
        max_contracts=12,
        max_position_size=12,
        profit_target=6_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 3),
            ScalingTier(2_000, 5),
            ScalingTier(3_000, 8),
            ScalingTier(5_000, 12),
        ),
        min_trading_days=10,
    ),
    "150k_opt2": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $150K Option 2 (EOD)",
        account_size=150_000,
        daily_loss_limit=-3_300,
        max_drawdown=-4_500,
        trailing_drawdown=False,
        eod_drawdown=True,
        max_contracts=15,
        max_position_size=15,
        profit_target=9_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 5),
            ScalingTier(4_000, 8),
            ScalingTier(8_000, 10),
            ScalingTier(12_000, 15),
        ),
        min_trading_days=10,
    ),
    "250k_opt2": PropFirmAccountRules(
        firm=PropFirmType.BULENOX,
        plan_name="Bulenox $250K Option 2 (EOD)",
        account_size=250_000,
        daily_loss_limit=-4_500,
        max_drawdown=-5_500,
        trailing_drawdown=False,
        eod_drawdown=True,
        max_contracts=25,
        max_position_size=25,
        profit_target=15_000,
        no_overnight=False,
        consistency_rule=True,
        consistency_pct=0.40,
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 6),
            ScalingTier(5_000, 12),
            ScalingTier(12_000, 18),
            ScalingTier(20_000, 25),
        ),
        min_trading_days=10,
    ),
}


# ═══════════════════════════════════════════════════════════════
# TRADEIFY — Futures (CME) — tradeify.co
# Compte Growth (eval standard) — pas de daily limit sur eval
# Piege: scaling funded (commence petit), consistency 35% funded,
# drawdown EOD trailing qui lock a break-even + $100
# ═══════════════════════════════════════════════════════════════

TRADEIFY_PLANS: Dict[str, PropFirmAccountRules] = {
    # ── Growth (eval + funded) ──
    "50k": PropFirmAccountRules(
        firm=PropFirmType.TRADEIFY,
        plan_name="Tradeify Growth $50K",
        account_size=50_000,
        daily_loss_limit=-1_250,
        max_drawdown=-2_000,
        trailing_drawdown=False,
        eod_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=4,
        max_position_size=4,
        profit_target=3_000,
        no_overnight=True,             # Eval: flat avant 4:59 PM ET
        consistency_rule=True,
        consistency_pct=0.35,           # 35% funded
        min_trading_days=1,             # 1 jour suffit pour passer
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 2),
            ScalingTier(1_500, 2),
            ScalingTier(2_000, 4),
        ),
        notes="Eval: pas de consistency. Funded: 35%, scaling, 7 jours min entre payouts.",
    ),
    "100k": PropFirmAccountRules(
        firm=PropFirmType.TRADEIFY,
        plan_name="Tradeify Growth $100K",
        account_size=100_000,
        daily_loss_limit=-2_500,
        max_drawdown=-3_500,
        trailing_drawdown=False,
        eod_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=8,
        max_position_size=8,
        profit_target=6_000,
        no_overnight=True,
        consistency_rule=True,
        consistency_pct=0.35,
        min_trading_days=1,
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 3),
            ScalingTier(1_500, 4),
            ScalingTier(2_000, 5),
            ScalingTier(3_000, 8),
        ),
    ),
    "150k": PropFirmAccountRules(
        firm=PropFirmType.TRADEIFY,
        plan_name="Tradeify Growth $150K",
        account_size=150_000,
        daily_loss_limit=-3_750,
        max_drawdown=-5_000,
        trailing_drawdown=False,
        eod_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=12,
        max_position_size=12,
        profit_target=9_000,
        no_overnight=True,
        consistency_rule=True,
        consistency_pct=0.35,
        min_trading_days=1,
        scaling_plan=True,
        scaling_tiers=(
            ScalingTier(0, 3),
            ScalingTier(1_500, 4),
            ScalingTier(2_000, 5),
            ScalingTier(3_000, 8),
            ScalingTier(4_500, 12),
        ),
    ),
}


# ═══════════════════════════════════════════════════════════════
# TAKE PROFIT TRADER (TPT) — Futures (CME) — takeprofittrader.com
# Piege principal: PRO = trailing intraday (beaucoup plus strict que eval)
# Pas de daily loss limit (supprimee jan 2025)
# Consistency 50% en eval, 0% en PRO
# News ban en PRO (1 min avant/apres Tier 1)
# Pas de bots/EAs autorises
# ═══════════════════════════════════════════════════════════════

TPT_PLANS: Dict[str, PropFirmAccountRules] = {
    "25k": PropFirmAccountRules(
        firm=PropFirmType.TPT,
        plan_name="TPT $25K",
        account_size=25_000,
        daily_loss_limit=0,             # PAS de daily loss limit
        max_drawdown=-1_500,
        trailing_drawdown=True,
        eod_drawdown=True,              # EOD en eval, intraday en PRO
        dd_locks_at_breakeven=True,     # Stop de trailer au break-even
        max_contracts=3,
        max_position_size=3,
        profit_target=1_500,
        no_overnight=True,              # Flat avant 5 PM ET
        no_news_trading=True,           # Ban en PRO (1 min avant/apres)
        news_blackout_seconds=60,
        consistency_rule=True,
        consistency_pct=0.50,           # 50% max en eval, 0% en PRO
        min_trading_days=5,
        payout_buffer=1_500,            # Cushion PRO
        notes="Eval=EOD DD. PRO=intraday trailing (BEAUCOUP plus strict). Pas de bots.",
    ),
    "50k": PropFirmAccountRules(
        firm=PropFirmType.TPT,
        plan_name="TPT $50K",
        account_size=50_000,
        daily_loss_limit=0,
        max_drawdown=-2_000,
        trailing_drawdown=True,
        eod_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=6,
        max_position_size=6,
        profit_target=3_000,
        no_overnight=True,
        no_news_trading=True,
        news_blackout_seconds=60,
        consistency_rule=True,
        consistency_pct=0.50,
        min_trading_days=5,
        payout_buffer=2_000,
        notes="Eval=EOD DD. PRO=intraday trailing. Pas de bots.",
    ),
    "75k": PropFirmAccountRules(
        firm=PropFirmType.TPT,
        plan_name="TPT $75K",
        account_size=75_000,
        daily_loss_limit=0,
        max_drawdown=-3_000,
        trailing_drawdown=True,
        eod_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=9,
        max_position_size=9,
        profit_target=4_500,
        no_overnight=True,
        no_news_trading=True,
        news_blackout_seconds=60,
        consistency_rule=True,
        consistency_pct=0.50,
        min_trading_days=5,
        payout_buffer=2_500,
    ),
    "100k": PropFirmAccountRules(
        firm=PropFirmType.TPT,
        plan_name="TPT $100K",
        account_size=100_000,
        daily_loss_limit=0,
        max_drawdown=-4_000,
        trailing_drawdown=True,
        eod_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=12,
        max_position_size=12,
        profit_target=6_000,
        no_overnight=True,
        no_news_trading=True,
        news_blackout_seconds=60,
        consistency_rule=True,
        consistency_pct=0.50,
        min_trading_days=5,
        payout_buffer=3_000,
    ),
    "150k": PropFirmAccountRules(
        firm=PropFirmType.TPT,
        plan_name="TPT $150K",
        account_size=150_000,
        daily_loss_limit=0,
        max_drawdown=-4_500,
        trailing_drawdown=True,
        eod_drawdown=True,
        dd_locks_at_breakeven=True,
        max_contracts=15,
        max_position_size=15,
        profit_target=9_000,
        no_overnight=True,
        no_news_trading=True,
        news_blackout_seconds=60,
        consistency_rule=True,
        consistency_pct=0.50,
        min_trading_days=5,
        payout_buffer=4_500,
    ),
}


# ═══════════════════════════════════════════════════════════════
# REGISTRY — Acces unifie
# ═══════════════════════════════════════════════════════════════

ALL_FIRMS: Dict[PropFirmType, Dict[str, PropFirmAccountRules]] = {
    PropFirmType.TOPSTEP: TOPSTEP_PLANS,
    PropFirmType.APEX: APEX_PLANS,
    PropFirmType.BULENOX: BULENOX_PLANS,
    PropFirmType.TRADEIFY: TRADEIFY_PLANS,
    PropFirmType.TPT: TPT_PLANS,
}


def get_prop_firm_rules(firm: str, plan: str) -> PropFirmAccountRules:
    """
    Recupere les regles d'un compte prop firm.

    Args:
        firm: "topstep", "apex", "bulenox", "tradeify", "tpt"
        plan: "25k", "50k", "50k_opt1", "50k_opt2", etc.

    Returns:
        PropFirmAccountRules (frozen, immuable)
    """
    try:
        firm_type = PropFirmType(firm.lower())
    except ValueError:
        available = [f.value for f in PropFirmType if f != PropFirmType.CUSTOM]
        raise ValueError(
            f"Prop firm inconnue: '{firm}'. "
            f"Disponibles: {', '.join(available)}"
        )

    plans = ALL_FIRMS.get(firm_type)
    if not plans:
        raise ValueError(f"Pas de plans pour {firm}")

    rules = plans.get(plan.lower())
    if not rules:
        raise ValueError(
            f"Plan inconnu: '{plan}' pour {firm}. "
            f"Disponibles: {', '.join(sorted(plans.keys()))}"
        )
    return rules


def list_available_plans() -> Dict[str, List[str]]:
    """Liste toutes les prop firms et leurs plans disponibles."""
    return {
        firm.value: sorted(plans.keys())
        for firm, plans in ALL_FIRMS.items()
    }


def create_custom_rules(
    plan_name: str,
    account_size: float,
    daily_loss_limit: float,
    max_drawdown: float,
    trailing_drawdown: bool = True,
    max_contracts: int = 10,
    **kwargs,
) -> PropFirmAccountRules:
    """Cree des regles custom pour un compte perso ou une firm non supportee."""
    return PropFirmAccountRules(
        firm=PropFirmType.CUSTOM,
        plan_name=plan_name,
        account_size=account_size,
        daily_loss_limit=daily_loss_limit,
        max_drawdown=max_drawdown,
        trailing_drawdown=trailing_drawdown,
        max_contracts=max_contracts,
        max_position_size=kwargs.get("max_position_size", max_contracts),
        profit_target=kwargs.get("profit_target"),
        no_overnight=kwargs.get("no_overnight", True),
        no_weekend=kwargs.get("no_weekend", True),
        no_news_trading=kwargs.get("no_news_trading", False),
        consistency_rule=kwargs.get("consistency_rule", False),
        consistency_pct=kwargs.get("consistency_pct", 0.0),
        min_trading_days=kwargs.get("min_trading_days", 0),
        daily_trade_limit=kwargs.get("daily_trade_limit"),
    )
