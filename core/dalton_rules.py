"""
Dalton Market Profile Rules — Extraites de "Mind Over Markets" (J. Dalton)
Règles IF/THEN codables pour classification de journée, signaux d'entrée/sortie,
et Special Situations.

Référence : Mind Over Markets, Chapters 2-4
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


# ============================================================
# 1. TYPES DE JOURNÉES (Chapter 2, p.20-30)
# ============================================================

class DayType(Enum):
    TREND = "trend"                          # Trend Day
    DOUBLE_DISTRIBUTION = "double_dist"       # Double-Distribution Trend Day
    NORMAL = "normal"                         # Normal Day
    NORMAL_VARIATION = "normal_variation"      # Normal Variation of Normal Day
    NEUTRAL_CENTER = "neutral_center"         # Neutral Day — close au centre
    NEUTRAL_EXTREME = "neutral_extreme"       # Neutral Day — close sur extrême
    NONTREND = "nontrend"                     # Nontrend Day (pas de conviction)
    NONCONVICTION = "nonconviction"           # Nonconviction Day (range trompeuse)


class OpenType(Enum):
    OPEN_DRIVE = "open_drive"                 # Open-Drive (p.63)
    OPEN_TEST_DRIVE = "open_test_drive"       # Open-Test-Drive (p.65)
    OPEN_REJECTION_REVERSE = "open_rej_rev"   # Open-Rejection-Reverse (p.68)
    OPEN_AUCTION_IN_RANGE = "open_auction_ir"  # Open-Auction dans le range (p.70)
    OPEN_AUCTION_OUT_RANGE = "open_auction_or" # Open-Auction hors range (p.71)


class ProfileShape(Enum):
    D_SHAPE = "d_shape"         # Profil en D = marché balancé
    P_SHAPE = "p_shape"         # Profil en P = buying, accumulation en haut
    B_SHAPE = "b_shape"         # Profil en b = selling, accumulation en bas
    THIN = "thin"               # Profil fin/allongé = marché imbalancé/trend


@dataclass
class MarketContext:
    """Contexte de marché nécessaire pour appliquer les règles Dalton."""
    # Initial Balance (première heure)
    ib_high: float              # Plus haut de l'Initial Balance
    ib_low: float               # Plus bas de l'Initial Balance
    ib_range: float             # ib_high - ib_low

    # Session actuelle
    session_high: float
    session_low: float
    current_price: float
    close_price: float          # Prix de clôture (ou dernier prix)

    # Volume Profile session
    vpoc: float                 # Volume Point of Control
    vah: float                  # Value Area High (70% du volume)
    val: float                  # Value Area Low (70% du volume)

    # Previous day
    prev_vah: float
    prev_val: float
    prev_high: float
    prev_low: float
    prev_vpoc: float
    prev_range: float           # prev_high - prev_low

    # Indicators
    range_ext_up: bool          # Range extension au-dessus de l'IB
    range_ext_down: bool        # Range extension en-dessous de l'IB
    max_tpo_width: int          # Largeur max du profil en TPOs
    buying_tail_len: int        # Longueur du buying tail (en TPOs)
    selling_tail_len: int       # Longueur du selling tail (en TPOs)
    single_prints_exist: bool   # Single prints entre 2 distributions

    # Delta / Imbalance
    tpo_count_above_poc: int    # TPOs au-dessus du POC
    tpo_count_below_poc: int    # TPOs en-dessous du POC
    cumulative_delta: float


# ============================================================
# 2. CLASSIFICATION DU TYPE DE JOURNÉE
#    (Chapter 2, p.20-30, Figure 2-10)
# ============================================================

def classify_day_type(ctx: MarketContext) -> DayType:
    """
    Dalton p.29 — Day Type Summary (Figure 2-10):
    Conviction croissante: Nontrend < Neutral < Normal < Normal Variation < Trend

    IF IB étroit AND pas de range extension → NONTREND
    IF IB moyen AND range extension des 2 côtés AND close au centre → NEUTRAL_CENTER
    IF IB moyen AND range extension des 2 côtés AND close sur extrême → NEUTRAL_EXTREME
    IF IB large AND pas de range extension → NORMAL
    IF IB moyen AND range extension 1 côté → NORMAL_VARIATION
    IF IB étroit AND range extension continue 1 côté AND profil fin (<=5 TPOs) → TREND
    IF IB étroit AND range extension + single prints + 2 distributions → DOUBLE_DISTRIBUTION
    """

    ib_pct_of_range = ctx.ib_range / max(ctx.session_high - ctx.session_low, 0.01)
    session_range = ctx.session_high - ctx.session_low
    close_position = (ctx.close_price - ctx.session_low) / max(session_range, 0.01)

    # --- Nontrend Day (p.27) ---
    # IB étroit, PAS de range extension, faible activité
    if ib_pct_of_range > 0.85 and not ctx.range_ext_up and not ctx.range_ext_down:
        if ctx.max_tpo_width <= 4:
            return DayType.NONTREND

    # --- Trend Day (p.22-25) ---
    # IB étroit, range extension continue dans 1 direction, profil fin (<=5 TPOs)
    if ctx.max_tpo_width <= 5:
        if ctx.range_ext_up and not ctx.range_ext_down:
            return DayType.TREND
        if ctx.range_ext_down and not ctx.range_ext_up:
            return DayType.TREND

    # --- Double-Distribution Trend Day (p.25) ---
    # IB étroit initial, puis forte extension, single prints entre 2 zones
    if ctx.single_prints_exist and (ctx.range_ext_up or ctx.range_ext_down):
        if ib_pct_of_range < 0.4:
            return DayType.DOUBLE_DISTRIBUTION

    # --- Neutral Day (p.27-29) ---
    # Range extension des 2 côtés de l'IB
    if ctx.range_ext_up and ctx.range_ext_down:
        # Neutral-Extreme: close sur un extrême (>80% ou <20%)
        if close_position > 0.80 or close_position < 0.20:
            return DayType.NEUTRAL_EXTREME
        # Neutral-Center: close au milieu
        return DayType.NEUTRAL_CENTER

    # --- Normal Day (p.20) ---
    # IB large (>60% du range), pas de disruption de l'IB
    if ib_pct_of_range > 0.60 and not ctx.range_ext_up and not ctx.range_ext_down:
        return DayType.NORMAL

    # --- Normal Variation (p.22) ---
    # IB moyen, range extension d'1 seul côté
    if ctx.range_ext_up != ctx.range_ext_down:  # un seul côté
        return DayType.NORMAL_VARIATION

    # Fallback
    return DayType.NONCONVICTION


# ============================================================
# 3. CLASSIFICATION DE LA FORME DU PROFIL
#    (p.20-30, p.49-57)
# ============================================================

def classify_profile_shape(ctx: MarketContext) -> ProfileShape:
    """
    IF profil symétrique (volume centré, VAH/VAL équilibrés) → D_SHAPE (balancé)
    IF volume concentré en haut (VPOC dans top 30%) → P_SHAPE (buying)
    IF volume concentré en bas (VPOC dans bottom 30%) → B_SHAPE (selling)
    IF profil fin/allongé (max_tpo_width <= 5) → THIN (trend/imbalancé)
    """

    session_range = max(ctx.session_high - ctx.session_low, 0.01)
    vpoc_position = (ctx.vpoc - ctx.session_low) / session_range

    # Profil fin = trend / imbalancé
    if ctx.max_tpo_width <= 5:
        return ProfileShape.THIN

    # P-shape: VPOC dans le tiers supérieur
    if vpoc_position > 0.70:
        return ProfileShape.P_SHAPE

    # b-shape: VPOC dans le tiers inférieur
    if vpoc_position < 0.30:
        return ProfileShape.B_SHAPE

    # D-shape: balancé, volume centré
    return ProfileShape.D_SHAPE


# ============================================================
# 4. CLASSIFICATION DE L'OUVERTURE
#    (Chapter 4, p.63-74)
# ============================================================

def classify_open_type(
    open_price: float,
    first_5min_high: float,
    first_5min_low: float,
    first_5min_drove_up: bool,
    first_5min_drove_down: bool,
    tested_then_reversed: bool,
    ctx: MarketContext,
) -> OpenType:
    """
    Dalton p.63-74 — Types d'ouverture, conviction décroissante:
    Open-Drive > Open-Test-Drive > Open-Rejection-Reverse > Open-Auction

    IF open + drive immédiat sans recul → OPEN_DRIVE
    IF open + test au-delà d'un ref + reversal → OPEN_TEST_DRIVE
    IF open + drive dans 1 dir + reversal à travers l'open → OPEN_REJECTION_REVERSE
    IF open dans le range précédent + rotations → OPEN_AUCTION_IN_RANGE
    IF open hors range précédent + rotations → OPEN_AUCTION_OUT_RANGE
    """

    in_prev_range = ctx.prev_low <= open_price <= ctx.prev_high
    in_prev_value = ctx.prev_val <= open_price <= ctx.prev_vah

    # Open-Drive: drive immédiat, pas de retour
    if first_5min_drove_up or first_5min_drove_down:
        if not tested_then_reversed:
            return OpenType.OPEN_DRIVE

    # Open-Test-Drive: test au-delà d'un ref, puis reversal fort
    if tested_then_reversed and (first_5min_drove_up or first_5min_drove_down):
        return OpenType.OPEN_TEST_DRIVE

    # Open-Rejection-Reverse: drive initial puis reversal à travers l'open
    if tested_then_reversed:
        return OpenType.OPEN_REJECTION_REVERSE

    # Open-Auction: rotations autour de l'open
    if in_prev_range:
        return OpenType.OPEN_AUCTION_IN_RANGE
    else:
        return OpenType.OPEN_AUCTION_OUT_RANGE


# ============================================================
# 5. SPECIAL SITUATIONS — Trades haute probabilité
#    (Chapter 4, p.272-310)
# ============================================================

@dataclass
class Signal:
    """Signal de trading généré par les règles Dalton."""
    action: str            # "BUY", "SELL", "HOLD", "EXIT", "STAY_OUT"
    confidence: float      # 0.0 à 1.0
    rule: str              # Nom de la règle Dalton
    stop_price: float = 0  # Stop loss suggéré
    target_price: float = 0
    reason: str = ""


def check_3i_day(ctx: MarketContext) -> Optional[Signal]:
    """
    Dalton p.273-276 — 3-I Day (Initiative tail + TPO count + Range extension)

    IF buying_tail >= 2 TPOs (initiative buying tail)
    AND tpo_count_below_poc > tpo_count_above_poc (TPOs favorisent acheteurs)
    AND range_ext_up (initiative buying range extension)
    → BUY — Le jour suivant a 94% de chances d'ouvrir au-dessus de la value area

    IF selling_tail >= 2 TPOs (initiative selling tail)
    AND tpo_count_above_poc > tpo_count_below_poc (TPOs favorisent vendeurs)
    AND range_ext_down (initiative selling range extension)
    → SELL — Le jour suivant a 94% de chances d'ouvrir en-dessous de la value area
    """

    # 3-I Buying Day
    if (ctx.buying_tail_len >= 2
            and ctx.tpo_count_below_poc > ctx.tpo_count_above_poc
            and ctx.range_ext_up):
        return Signal(
            action="BUY",
            confidence=0.94,
            rule="3-I Buying Day",
            stop_price=ctx.val,
            target_price=ctx.vah + (ctx.vah - ctx.val),
            reason="Initiative tail + TPO count + Range extension all bullish. "
                   "94% next day opens within/above value."
        )

    # 3-I Selling Day
    if (ctx.selling_tail_len >= 2
            and ctx.tpo_count_above_poc > ctx.tpo_count_below_poc
            and ctx.range_ext_down):
        return Signal(
            action="SELL",
            confidence=0.94,
            rule="3-I Selling Day",
            stop_price=ctx.vah,
            target_price=ctx.val - (ctx.vah - ctx.val),
            reason="Initiative tail + TPO count + Range extension all bearish. "
                   "94% next day opens within/below value."
        )

    return None


def check_neutral_extreme(ctx: MarketContext) -> Optional[Signal]:
    """
    Dalton p.277-278 — Neutral-Extreme Day

    IF range extension des 2 côtés (Neutral day)
    AND close sur l'extrême haut (>80% du range)
    → BUY — Buyer a gagné la bataille. 92% continuation le lendemain.

    IF range extension des 2 côtés (Neutral day)
    AND close sur l'extrême bas (<20% du range)
    → SELL — Seller a gagné. 92% continuation le lendemain.
    """

    if not (ctx.range_ext_up and ctx.range_ext_down):
        return None  # Pas un Neutral day

    session_range = max(ctx.session_high - ctx.session_low, 0.01)
    close_position = (ctx.close_price - ctx.session_low) / session_range

    if close_position > 0.80:
        return Signal(
            action="BUY",
            confidence=0.92,
            rule="Neutral-Extreme (close on highs)",
            stop_price=ctx.val,
            target_price=ctx.session_high + (ctx.vah - ctx.val) * 0.5,
            reason="Neutral day close on highs = buyer won. "
                   "92% next day within/above value."
        )

    if close_position < 0.20:
        return Signal(
            action="SELL",
            confidence=0.92,
            rule="Neutral-Extreme (close on lows)",
            stop_price=ctx.vah,
            target_price=ctx.session_low - (ctx.vah - ctx.val) * 0.5,
            reason="Neutral day close on lows = seller won. "
                   "92% next day within/below value."
        )

    return None


def check_value_area_rule(ctx: MarketContext) -> Optional[Signal]:
    """
    Dalton p.278-280 — Value Area Rule

    IF marché ouvre HORS de la value area précédente
    AND price ré-entre dans la prev value area (double TPOs = acceptance)
    → Trade dans la direction de la pénétration.
       Le prix va probablement traverser TOUTE la value area.

    Conditions de renforcement (p.280):
    1. Plus l'open est proche de la VA, plus les chances sont élevées
    2. VA étroite = plus facile à traverser
    3. Direction du trend long-terme = momentum
    """

    open_price = ctx.current_price  # Utiliser le prix d'ouverture en pratique

    # Prix a ouvert au-dessus de la prev VA et ré-entre dedans
    if open_price > ctx.prev_vah and ctx.current_price <= ctx.prev_vah:
        return Signal(
            action="SELL",
            confidence=0.70,
            rule="Value Area Rule (rejection from above)",
            stop_price=ctx.session_high,
            target_price=ctx.prev_val,
            reason="Opened above prev VA, re-entered. "
                   "Price likely to trade through entire VA to VAL."
        )

    # Prix a ouvert en-dessous de la prev VA et ré-entre dedans
    if open_price < ctx.prev_val and ctx.current_price >= ctx.prev_val:
        return Signal(
            action="BUY",
            confidence=0.70,
            rule="Value Area Rule (rejection from below)",
            stop_price=ctx.session_low,
            target_price=ctx.prev_vah,
            reason="Opened below prev VA, re-entered. "
                   "Price likely to trade through entire VA to VAH."
        )

    return None


def check_balance_area_breakout(ctx: MarketContext,
                                 balance_high: float,
                                 balance_low: float) -> Optional[Signal]:
    """
    Dalton p.288-292 — Balance Area Breakout

    IF prix casse au-dessus du balance area high (accepté, pas juste un probe)
    → BUY avec stop juste sous le balance high.

    IF prix casse en-dessous du balance area low (accepté)
    → SELL avec stop juste au-dessus du balance low.

    IF breakout échoue (prix revient dans la balance area)
    → REVERSE. Trade dans la direction opposée "avec conviction".
       "A balance area break-out is a trade you almost have to do." (p.292)
    """

    if ctx.current_price > balance_high:
        return Signal(
            action="BUY",
            confidence=0.75,
            rule="Balance Area Breakout (up)",
            stop_price=balance_high,
            target_price=balance_high + (balance_high - balance_low),
            reason="Price accepted above balance area. "
                   "Go with breakout. If rejected, reverse."
        )

    if ctx.current_price < balance_low:
        return Signal(
            action="SELL",
            confidence=0.75,
            rule="Balance Area Breakout (down)",
            stop_price=balance_low,
            target_price=balance_low - (balance_high - balance_low),
            reason="Price accepted below balance area. "
                   "Go with breakout. If rejected, reverse."
        )

    return None


def check_spike_rules(ctx: MarketContext,
                      spike_top: float,
                      spike_bottom: float,
                      spike_direction: str) -> Optional[Signal]:
    """
    Dalton p.280-288 — Spike Rules

    Un spike = breakout en fin de session (derniers 30-60 min).
    Le lendemain, on observe l'ouverture par rapport au spike:

    IF open DANS le spike → marché balance autour du spike.
       Range estimé = longueur du spike.

    IF open AU-DELÀ du spike (dans la direction) → continuation forte.
       → BUY (buying spike) ou SELL (selling spike). Stop = spike top/bottom.

    IF open OPPOSÉ au spike (rejet) → le spike est rejeté.
       → Trade CONTRE le spike. Le mouvement est terminé.

    IMPORTANT: Le spike top/bottom est un support/résistance fiable
    UNIQUEMENT pour le PREMIER test. (p.288)
    """

    spike_len = spike_top - spike_bottom

    if spike_direction == "up":
        # Open au-dessus du spike = continuation
        if ctx.current_price > spike_top:
            return Signal(
                action="BUY",
                confidence=0.80,
                rule="Spike continuation (buying spike, open above)",
                stop_price=spike_top,
                target_price=spike_top + spike_len,
                reason="Open above buying spike = extreme imbalance, "
                       "continuation expected."
            )
        # Open en-dessous du spike = rejet
        if ctx.current_price < spike_bottom:
            return Signal(
                action="SELL",
                confidence=0.70,
                rule="Spike rejection (buying spike rejected)",
                stop_price=spike_top,
                target_price=ctx.prev_val,
                reason="Open below buying spike = rejection. "
                       "Spike probe is over."
            )

    elif spike_direction == "down":
        if ctx.current_price < spike_bottom:
            return Signal(
                action="SELL",
                confidence=0.80,
                rule="Spike continuation (selling spike, open below)",
                stop_price=spike_bottom,
                target_price=spike_bottom - spike_len,
                reason="Open below selling spike = extreme imbalance, "
                       "continuation expected."
            )
        if ctx.current_price > spike_top:
            return Signal(
                action="BUY",
                confidence=0.70,
                rule="Spike rejection (selling spike rejected)",
                stop_price=spike_bottom,
                target_price=ctx.prev_vah,
                reason="Open above selling spike = rejection. "
                       "Spike probe is over."
            )

    return None


def check_gap_rules(ctx: MarketContext) -> Optional[Signal]:
    """
    Dalton p.292-298 — Gap Rules

    Un gap = ouverture hors du range précédent.

    IF gap up AND pas de retracement dans les 60 premières minutes
    → BUY. Le gap est confirmé. Stop = bas du gap (prev_high).

    IF gap up AND retracement qui comble le gap dans les 60 premières minutes
    → Le gap est rejeté. Responsive sellers présents. EXIT longs.

    IF gap down AND pas de retracement
    → SELL. Stop = haut du gap (prev_low).

    "The longer a gap holds, the greater the probability of continuation." (p.293)
    "If a gap is going to be retraced, the rejection will usually fill the gap
     within the first hour." (p.293)
    """

    # Gap up
    if ctx.session_low > ctx.prev_high:
        return Signal(
            action="BUY",
            confidence=0.72,
            rule="Gap Up (holding)",
            stop_price=ctx.prev_high,
            target_price=ctx.session_high + (ctx.session_high - ctx.prev_high),
            reason="Gap up holding beyond first hour. "
                   "Trade with initiative activity. Stop if gap fills."
        )

    # Gap down
    if ctx.session_high < ctx.prev_low:
        return Signal(
            action="SELL",
            confidence=0.72,
            rule="Gap Down (holding)",
            stop_price=ctx.prev_low,
            target_price=ctx.session_low - (ctx.prev_low - ctx.session_low),
            reason="Gap down holding beyond first hour. "
                   "Trade with initiative activity. Stop if gap fills."
        )

    return None


# ============================================================
# 6. MEAN REVERSION / FAKE BREAKOUT
#    (p.49-57, p.288-292)
# ============================================================

def check_fake_breakout(ctx: MarketContext,
                        balance_high: float,
                        balance_low: float) -> Optional[Signal]:
    """
    Dalton p.288-292 — Fake Breakout (Balance Area)

    IF prix casse au-dessus du balance high
    AND PAS de continuation (pas de nouveaux acheteurs)
    AND prix revient DANS la balance area
    → SELL (reversal). "The opposite participant can enter with conviction,
       driving price with strong directional conviction." (p.292)

    IF prix casse en-dessous du balance low
    AND PAS de continuation
    AND prix revient DANS la balance area
    → BUY (reversal).

    "Shorter-term traders auction price beyond a known reference point
    to see if there is new activity to sustain the price movement.
    If there is no response, then the opposite participant can enter
    the market with confidence." (p.292)
    """

    # Failed breakout up: prix au-dessus du high mais revient
    if ctx.session_high > balance_high and ctx.current_price < balance_high:
        return Signal(
            action="SELL",
            confidence=0.78,
            rule="Failed Breakout Up (mean reversion)",
            stop_price=ctx.session_high,
            target_price=balance_low,
            reason="Breakout above balance area failed. No follow-through. "
                   "Reverse with conviction toward balance low."
        )

    # Failed breakout down: prix en-dessous du low mais revient
    if ctx.session_low < balance_low and ctx.current_price > balance_low:
        return Signal(
            action="BUY",
            confidence=0.78,
            rule="Failed Breakout Down (mean reversion)",
            stop_price=ctx.session_low,
            target_price=balance_high,
            reason="Breakout below balance area failed. No follow-through. "
                   "Reverse with conviction toward balance high."
        )

    return None


def check_responsive_activity(ctx: MarketContext) -> Optional[Signal]:
    """
    Dalton p.46-49 — Initiative vs Responsive Activity

    IF prix est NETTEMENT au-dessus de la prev VAH (>2x IB range)
    → Responsive selling probable. Risque de mean reversion.

    IF prix est NETTEMENT en-dessous de la prev VAL (>2x IB range)
    → Responsive buying probable. Risque de mean reversion.

    IF prix est dans la prev value area → Balanced, pas de signal fort.
    """

    distance_above = ctx.current_price - ctx.prev_vah
    distance_below = ctx.prev_val - ctx.current_price

    # Responsive selling: prix très au-dessus de la value
    if distance_above > ctx.ib_range * 2:
        return Signal(
            action="SELL",
            confidence=0.65,
            rule="Responsive Selling Zone",
            stop_price=ctx.session_high + ctx.ib_range * 0.5,
            target_price=ctx.prev_vah,
            reason="Price extended well above value. "
                   "Responsive sellers expected to return price to value."
        )

    # Responsive buying: prix très en-dessous de la value
    if distance_below > ctx.ib_range * 2:
        return Signal(
            action="BUY",
            confidence=0.65,
            rule="Responsive Buying Zone",
            stop_price=ctx.session_low - ctx.ib_range * 0.5,
            target_price=ctx.prev_val,
            reason="Price extended well below value. "
                   "Responsive buyers expected to return price to value."
        )

    return None


# ============================================================
# 7. NIVEAUX CLÉS — Support / Résistance
#    (p.11-15, p.42-44, p.278-288)
# ============================================================

@dataclass
class KeyLevels:
    """Niveaux clés Dalton pour le trading."""
    vpoc: float                 # Volume POC = prix le plus échangé = aimant
    vah: float                  # Value Area High = résistance
    val: float                  # Value Area Low = support
    ib_high: float              # Initial Balance High = ref pour breakout
    ib_low: float               # Initial Balance Low = ref pour breakout
    prev_vpoc: float            # POC de la veille = aimant
    prev_vah: float             # VAH veille = support/résistance
    prev_val: float             # VAL veille = support/résistance


def get_key_levels(ctx: MarketContext) -> KeyLevels:
    return KeyLevels(
        vpoc=ctx.vpoc,
        vah=ctx.vah,
        val=ctx.val,
        ib_high=ctx.ib_high,
        ib_low=ctx.ib_low,
        prev_vpoc=ctx.prev_vpoc,
        prev_vah=ctx.prev_vah,
        prev_val=ctx.prev_val,
    )


def check_price_vs_levels(ctx: MarketContext) -> Signal:
    """
    Règles de prix par rapport aux niveaux (synthèse p.11-15, p.278):

    IF price > VAH → Initiative buying, trend up probable
    IF price < VAL → Initiative selling, trend down probable
    IF price near VPOC (±1 tick) → Marché balancé, mean reversion likely
    IF price between VAL and VPOC → Slight bearish bias
    IF price between VPOC and VAH → Slight bullish bias

    IF price enters prev_val from below → Value Area Rule: probable traverse up
    IF price enters prev_vah from above → Value Area Rule: probable traverse down
    """

    va_range = max(ctx.vah - ctx.val, 0.01)
    near_vpoc = abs(ctx.current_price - ctx.vpoc) < va_range * 0.05

    if near_vpoc:
        return Signal(
            action="HOLD",
            confidence=0.50,
            rule="At VPOC (balanced)",
            reason="Price at VPOC = market balanced. Wait for direction."
        )

    if ctx.current_price > ctx.vah:
        return Signal(
            action="BUY",
            confidence=0.60,
            rule="Price above VAH (initiative buying)",
            stop_price=ctx.vah,
            target_price=ctx.vah + va_range,
            reason="Price accepted above Value Area = initiative buying."
        )

    if ctx.current_price < ctx.val:
        return Signal(
            action="SELL",
            confidence=0.60,
            rule="Price below VAL (initiative selling)",
            stop_price=ctx.val,
            target_price=ctx.val - va_range,
            reason="Price accepted below Value Area = initiative selling."
        )

    if ctx.current_price > ctx.vpoc:
        return Signal(
            action="BUY",
            confidence=0.52,
            rule="Price between VPOC and VAH (slight bullish)",
            reason="Price above VPOC within value = slight bullish bias."
        )

    return Signal(
        action="SELL",
        confidence=0.52,
        rule="Price between VAL and VPOC (slight bearish)",
        reason="Price below VPOC within value = slight bearish bias."
    )


# ============================================================
# 8. STAY OUT — Marchés à éviter
#    (Chapter 4, p.300-304)
# ============================================================

def should_stay_out(ctx: MarketContext) -> Optional[Signal]:
    """
    Dalton p.300-304 — Markets to Stay Out Of

    IF Nontrend day (IB étroit, pas de range extension, peu de volume)
    → STAY_OUT. "The most obvious market to stay out of." (p.300)

    IF Nonconviction day (rotations random, pas de ref points)
    → STAY_OUT. "It is best to stay out of the market altogether." (p.301)

    IF IB très étroit AND aucune extension après 2h
    → STAY_OUT. Attendre que le marché donne des infos.
    """

    day_type = classify_day_type(ctx)

    if day_type == DayType.NONTREND:
        return Signal(
            action="STAY_OUT",
            confidence=0.90,
            rule="Nontrend Day — No trade",
            reason="Nontrend day: no facilitation, no conviction, no opportunity."
        )

    if day_type == DayType.NONCONVICTION:
        return Signal(
            action="STAY_OUT",
            confidence=0.85,
            rule="Nonconviction Day — No trade",
            reason="Nonconviction day: random rotations, no reference points. "
                   "Forcing trades leads to losses."
        )

    return None


# ============================================================
# 9. RANGE ESTIMATION
#    (Chapter 4, p.74-87)
# ============================================================

def estimate_daily_range(ctx: MarketContext) -> dict:
    """
    Dalton p.74-87 — Estimation du range journalier

    IF open dans la value area précédente → Range ~= range de la veille
    IF open hors value mais dans range → Range > range veille, conviction modérée
    IF open hors range (gap) → Range illimité dans la direction de l'initiative

    Méthode:
    1. Identifier l'extrême qui tient (buying tail ou selling tail)
    2. Superposer la longueur du range de la veille depuis cet extrême
    """

    open_in_value = ctx.prev_val <= ctx.current_price <= ctx.prev_vah
    open_in_range = ctx.prev_low <= ctx.current_price <= ctx.prev_high
    open_outside_range = not open_in_range

    if open_in_value:
        # "the developing range will rarely exceed the length
        #  of the previous day's range" (p.76)
        estimated_high = ctx.session_low + ctx.prev_range
        estimated_low = ctx.session_high - ctx.prev_range
        return {
            'bias': 'balanced',
            'estimated_range': ctx.prev_range,
            'estimated_high': estimated_high,
            'estimated_low': estimated_low,
            'note': "Open in value: expect range similar to yesterday."
        }

    if open_in_range and not open_in_value:
        return {
            'bias': 'moderate_imbalance',
            'estimated_range': ctx.prev_range * 1.2,
            'estimated_high': ctx.session_low + ctx.prev_range * 1.2,
            'estimated_low': ctx.session_high - ctx.prev_range * 1.2,
            'note': "Open outside value but in range: moderate imbalance."
        }

    if open_outside_range:
        direction = 'up' if ctx.current_price > ctx.prev_high else 'down'
        return {
            'bias': f'strong_imbalance_{direction}',
            'estimated_range': None,  # unlimited
            'estimated_high': None,
            'estimated_low': None,
            'note': f"Open outside range ({direction}): unlimited range potential. "
                    f"Trend day likely."
        }

    return {'bias': 'unknown', 'estimated_range': ctx.prev_range}


# ============================================================
# 10. ORCHESTRATEUR — Évalue TOUTES les règles
# ============================================================

def evaluate_all_rules(ctx: MarketContext,
                       balance_high: float = 0,
                       balance_low: float = 0,
                       spike_top: float = 0,
                       spike_bottom: float = 0,
                       spike_direction: str = "") -> list[Signal]:
    """
    Évalue toutes les règles Dalton et retourne une liste de signaux,
    triés par confidence décroissante.
    """

    signals = []

    # Check si on devrait rester dehors
    stay_out = should_stay_out(ctx)
    if stay_out:
        return [stay_out]

    # Special Situations (haute probabilité)
    sig = check_3i_day(ctx)
    if sig:
        signals.append(sig)

    sig = check_neutral_extreme(ctx)
    if sig:
        signals.append(sig)

    sig = check_value_area_rule(ctx)
    if sig:
        signals.append(sig)

    if balance_high and balance_low:
        sig = check_balance_area_breakout(ctx, balance_high, balance_low)
        if sig:
            signals.append(sig)

        sig = check_fake_breakout(ctx, balance_high, balance_low)
        if sig:
            signals.append(sig)

    if spike_top and spike_bottom and spike_direction:
        sig = check_spike_rules(ctx, spike_top, spike_bottom, spike_direction)
        if sig:
            signals.append(sig)

    sig = check_gap_rules(ctx)
    if sig:
        signals.append(sig)

    # Indicateurs complémentaires
    sig = check_responsive_activity(ctx)
    if sig:
        signals.append(sig)

    sig = check_price_vs_levels(ctx)
    if sig:
        signals.append(sig)

    # Tri par confiance décroissante
    signals.sort(key=lambda s: s.confidence, reverse=True)

    return signals
