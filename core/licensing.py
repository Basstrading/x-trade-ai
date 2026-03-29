"""
Licensing & Subscription System
=================================
Gere les licences, les abonnements Stripe, et les codes promo.

Modele :
- 1 licence = 1 compte prop firm = $29/mois
- Copie de comptes : chaque compte copie = 1 licence supplementaire
- Codes promo : acces gratuit (pour les eleves, etc.)
- Trial : 7 jours gratuits, 1 compte max

Stripe :
- Checkout Session → paiement → webhook → licence activee
- Subscription → facturation recurrente
- Customer Portal → le client gere son abo
"""

import json
import secrets
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


LICENSES_DIR = Path(__file__).parent.parent / "data" / "licenses"
PROMO_FILE = LICENSES_DIR / "promo_codes.json"


@dataclass
class License:
    """Une licence pour un compte prop firm."""
    license_key: str
    trader_id: str
    email: str = ""
    # Type
    license_type: str = "trial"          # trial, paid, promo, admin
    # Compte associe
    firm: str = ""
    plan: str = ""
    account_label: str = ""              # Ex: "Topstep 50K #1"
    # Dates
    created_at: str = ""
    expires_at: str = ""                 # Vide = pas d'expiration
    # Stripe
    stripe_customer_id: str = ""
    stripe_subscription_id: str = ""
    # Promo
    promo_code: str = ""
    # Status
    active: bool = True
    # Copy accounts (IDs des comptes copies)
    copy_account_ids: List[str] = field(default_factory=list)

    def is_valid(self) -> bool:
        """Verifie si la licence est valide."""
        if not self.active:
            return False
        if self.expires_at:
            try:
                exp = datetime.fromisoformat(self.expires_at)
                if datetime.now() > exp:
                    return False
            except ValueError:
                pass
        return True

    def days_remaining(self) -> int:
        """Jours restants avant expiration."""
        if not self.expires_at:
            return 999  # Pas d'expiration
        try:
            exp = datetime.fromisoformat(self.expires_at)
            delta = exp - datetime.now()
            return max(0, delta.days)
        except ValueError:
            return 0


@dataclass
class PromoCode:
    """Code promo pour acces gratuit."""
    code: str
    created_by: str = "admin"
    duration_days: int = 30              # Duree de l'acces
    max_uses: int = 1                    # Nombre d'utilisations max
    uses: int = 0
    active: bool = True
    note: str = ""                       # Ex: "Pour les eleves formation mars 2026"
    created_at: str = ""


class LicenseManager:
    """
    Gere toutes les licences et codes promo.
    """

    def __init__(self):
        LICENSES_DIR.mkdir(parents=True, exist_ok=True)
        self._licenses: Dict[str, License] = {}
        self._promo_codes: Dict[str, PromoCode] = {}
        self._load()

    # ── Licences ──

    def create_trial(self, trader_id: str, email: str = "",
                     firm: str = "", plan: str = "") -> License:
        """Cree une licence trial (7 jours, 1 compte)."""
        key = self._generate_key("TRL")
        lic = License(
            license_key=key,
            trader_id=trader_id,
            email=email,
            license_type="trial",
            firm=firm,
            plan=plan,
            account_label=f"{firm} {plan} (trial)",
            created_at=datetime.now().isoformat(),
            expires_at=(datetime.now() + timedelta(days=7)).isoformat(),
        )
        self._licenses[key] = lic
        self._save()
        logger.info(f"Trial cree: {key} pour {trader_id} ({firm} {plan})")
        return lic

    def create_paid(self, trader_id: str, email: str,
                    firm: str, plan: str,
                    stripe_customer_id: str = "",
                    stripe_subscription_id: str = "") -> License:
        """Cree une licence payante ($29/mois)."""
        key = self._generate_key("PRO")
        lic = License(
            license_key=key,
            trader_id=trader_id,
            email=email,
            license_type="paid",
            firm=firm,
            plan=plan,
            account_label=f"{firm} {plan}",
            created_at=datetime.now().isoformat(),
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
        )
        self._licenses[key] = lic
        self._save()
        logger.info(f"Licence payante: {key} pour {email} ({firm} {plan})")
        return lic

    def activate_promo(self, trader_id: str, code: str, email: str = "",
                       firm: str = "", plan: str = "") -> Optional[License]:
        """Active un code promo et cree une licence."""
        promo = self._promo_codes.get(code.upper())
        if not promo:
            logger.warning(f"Code promo inconnu: {code}")
            return None
        if not promo.active:
            logger.warning(f"Code promo desactive: {code}")
            return None
        if promo.uses >= promo.max_uses:
            logger.warning(f"Code promo epuise: {code} ({promo.uses}/{promo.max_uses})")
            return None

        key = self._generate_key("GFT")
        lic = License(
            license_key=key,
            trader_id=trader_id,
            email=email,
            license_type="promo",
            firm=firm,
            plan=plan,
            account_label=f"{firm} {plan} (promo)",
            created_at=datetime.now().isoformat(),
            expires_at=(datetime.now() + timedelta(days=promo.duration_days)).isoformat(),
            promo_code=code,
        )
        self._licenses[key] = lic
        promo.uses += 1
        self._save()
        logger.info(f"Promo {code} active: {key} pour {trader_id} ({promo.duration_days}j)")
        return lic

    def validate_key(self, key: str) -> Optional[License]:
        """Valide une cle de licence."""
        lic = self._licenses.get(key)
        if not lic:
            return None
        if not lic.is_valid():
            return None
        return lic

    def deactivate(self, key: str):
        """Desactive une licence (annulation abo)."""
        lic = self._licenses.get(key)
        if lic:
            lic.active = False
            self._save()
            logger.info(f"Licence desactivee: {key}")

    def get_licenses_for_email(self, email: str) -> List[License]:
        """Toutes les licences d'un email."""
        return [l for l in self._licenses.values() if l.email == email]

    def get_all_active(self) -> List[License]:
        """Toutes les licences actives."""
        return [l for l in self._licenses.values() if l.is_valid()]

    # ── Copy accounts ──

    def add_copy_account(self, license_key: str, account_id: str) -> bool:
        """Ajoute un compte copie a une licence."""
        lic = self._licenses.get(license_key)
        if not lic or not lic.is_valid():
            return False
        if account_id not in lic.copy_account_ids:
            lic.copy_account_ids.append(account_id)
            self._save()
        return True

    def remove_copy_account(self, license_key: str, account_id: str):
        """Retire un compte copie."""
        lic = self._licenses.get(license_key)
        if lic and account_id in lic.copy_account_ids:
            lic.copy_account_ids.remove(account_id)
            self._save()

    # ── Codes promo ──

    def create_promo_code(self, duration_days: int = 30, max_uses: int = 1,
                          note: str = "", code: str = "") -> PromoCode:
        """Cree un code promo."""
        if not code:
            code = secrets.token_urlsafe(6).upper()
        promo = PromoCode(
            code=code,
            duration_days=duration_days,
            max_uses=max_uses,
            note=note,
            created_at=datetime.now().isoformat(),
        )
        self._promo_codes[code] = promo
        self._save()
        logger.info(f"Code promo cree: {code} ({duration_days}j, {max_uses} utilisations) — {note}")
        return promo

    def create_batch_promo(self, count: int, duration_days: int = 30,
                           note: str = "") -> List[PromoCode]:
        """Cree un lot de codes promo (pour une classe d'eleves)."""
        codes = []
        for _ in range(count):
            promo = self.create_promo_code(
                duration_days=duration_days, max_uses=1, note=note
            )
            codes.append(promo)
        logger.info(f"Lot de {count} codes promo crees ({duration_days}j)")
        return codes

    def list_promo_codes(self) -> List[dict]:
        """Liste tous les codes promo."""
        return [
            {
                "code": p.code,
                "duration_days": p.duration_days,
                "max_uses": p.max_uses,
                "uses": p.uses,
                "active": p.active,
                "note": p.note,
            }
            for p in self._promo_codes.values()
        ]

    def deactivate_promo(self, code: str):
        """Desactive un code promo."""
        promo = self._promo_codes.get(code.upper())
        if promo:
            promo.active = False
            self._save()

    # ── Stats ──

    def get_stats(self) -> dict:
        """Stats des licences."""
        all_lic = list(self._licenses.values())
        active = [l for l in all_lic if l.is_valid()]
        return {
            "total_licenses": len(all_lic),
            "active_licenses": len(active),
            "trials": len([l for l in active if l.license_type == "trial"]),
            "paid": len([l for l in active if l.license_type == "paid"]),
            "promo": len([l for l in active if l.license_type == "promo"]),
            "total_promo_codes": len(self._promo_codes),
            "active_promo_codes": len([p for p in self._promo_codes.values() if p.active]),
            "mrr": len([l for l in active if l.license_type == "paid"]) * 29,
        }

    # ── Persistence ──

    def _generate_key(self, prefix: str = "XTR") -> str:
        raw = secrets.token_urlsafe(12)
        return f"{prefix}-{raw}"

    def _save(self):
        try:
            # Licences
            data = {k: asdict(v) for k, v in self._licenses.items()}
            lic_file = LICENSES_DIR / "licenses.json"
            lic_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Promos
            promo_data = {k: asdict(v) for k, v in self._promo_codes.items()}
            PROMO_FILE.write_text(
                json.dumps(promo_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Erreur sauvegarde licences: {e}")

    def _load(self):
        # Licences
        lic_file = LICENSES_DIR / "licenses.json"
        if lic_file.exists():
            try:
                data = json.loads(lic_file.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._licenses[k] = License(**{
                        f: v[f] for f in License.__dataclass_fields__ if f in v
                    })
            except Exception as e:
                logger.warning(f"Erreur chargement licences: {e}")

        # Promos
        if PROMO_FILE.exists():
            try:
                data = json.loads(PROMO_FILE.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._promo_codes[k] = PromoCode(**{
                        f: v[f] for f in PromoCode.__dataclass_fields__ if f in v
                    })
            except Exception as e:
                logger.warning(f"Erreur chargement promos: {e}")
