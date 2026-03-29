"""
x-trade.ai — Installation Client
==================================
Ce script est ce que le client execute apres avoir paye (ou recu un code promo).

Parcours :
1. Entrer sa cle de licence ou son code promo
2. Choisir sa prop firm et son plan
3. Choisir son instrument
4. Le systeme configure tout automatiquement
5. Le Risk Desk demarre

Usage :
    python install.py
"""

import os
import sys
import json
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Assurer les dependances
def ensure_deps():
    deps = [
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn[standard]"),
        ("dotenv", "python-dotenv"),
        ("loguru", "loguru"),
        ("numpy", "numpy"),
    ]
    missing = []
    for mod, pkg in deps:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"  Installation des dependances...")
        os.system(f'{sys.executable} -m pip install -q {" ".join(missing)}')


FIRMS = {
    "topstep": {
        "name": "TopstepX",
        "plans": {
            "50k": "50K ($2,000 DD, 5 contrats)",
            "100k": "100K ($3,000 DD, 10 contrats)",
            "150k": "150K ($4,500 DD, 15 contrats)",
        },
    },
    "apex": {
        "name": "Apex Trader Funding (4.0)",
        "plans": {
            "25k": "25K ($1,000 DD, 4 contrats)",
            "50k": "50K ($2,000 DD, 6 contrats)",
            "100k": "100K ($3,000 DD, 8 contrats)",
            "150k": "150K ($4,000 DD, 12 contrats)",
        },
    },
    "bulenox": {
        "name": "Bulenox",
        "plans": {
            "25k_opt1": "25K Option 1 (Trailing, pas de daily limit)",
            "25k_opt2": "25K Option 2 (EOD, daily limit, scaling)",
            "50k_opt1": "50K Option 1 (Trailing)",
            "50k_opt2": "50K Option 2 (EOD, scaling)",
            "100k_opt1": "100K Option 1 (Trailing)",
            "100k_opt2": "100K Option 2 (EOD, scaling)",
            "150k_opt1": "150K Option 1 (Trailing)",
            "150k_opt2": "150K Option 2 (EOD, scaling)",
            "250k_opt1": "250K Option 1 (Trailing)",
            "250k_opt2": "250K Option 2 (EOD, scaling)",
        },
    },
    "tradeify": {
        "name": "Tradeify Growth",
        "plans": {
            "50k": "50K ($2,000 DD, 4 contrats)",
            "100k": "100K ($3,500 DD, 8 contrats)",
            "150k": "150K ($5,000 DD, 12 contrats)",
        },
    },
    "tpt": {
        "name": "TakeProfitTrader",
        "plans": {
            "25k": "25K ($1,500 DD, 3 contrats)",
            "50k": "50K ($2,000 DD, 6 contrats)",
            "75k": "75K ($3,000 DD, 9 contrats)",
            "100k": "100K ($4,000 DD, 12 contrats)",
            "150k": "150K ($4,500 DD, 15 contrats)",
        },
    },
}

INSTRUMENTS = {
    "MNQ": "Micro Nasdaq (MNQ) — $2/pt",
    "NQ": "E-mini Nasdaq (NQ) — $20/pt",
    "MES": "Micro S&P 500 (MES) — $5/pt",
    "ES": "E-mini S&P 500 (ES) — $50/pt",
    "CL": "Crude Oil (CL) — $1,000/pt",
    "MCL": "Micro Crude Oil (MCL) — $100/pt",
    "GC": "Gold (GC) — $100/pt",
    "MGC": "Micro Gold (MGC) — $10/pt",
    "YM": "E-mini Dow (YM) — $5/pt",
    "RTY": "E-mini Russell (RTY) — $50/pt",
    "6E": "Euro FX (6E) — $125,000/pt",
    "NG": "Natural Gas (NG) — $10,000/pt",
}


def header():
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║          x-trade.ai — Installation       ║")
    print("  ║     Your Risk Desk. Your Payout.         ║")
    print("  ╚══════════════════════════════════════════╝")
    print()


def step_license():
    """Etape 1 : Licence ou code promo."""
    print("  ── ETAPE 1 : Activation ──")
    print()
    print("  Vous avez :")
    print("    1. Une cle de licence (apres paiement)")
    print("    2. Un code promo (recu de votre formateur)")
    print("    3. Commencer un essai gratuit (7 jours)")
    print()

    choice = input("  Choix (1/2/3) : ").strip()

    if choice == "1":
        key = input("  Cle de licence : ").strip()
        return {"type": "license", "key": key}
    elif choice == "2":
        code = input("  Code promo : ").strip()
        name = input("  Votre nom/pseudo : ").strip() or "trader"
        email = input("  Votre email : ").strip()
        return {"type": "promo", "code": code, "name": name, "email": email}
    else:
        name = input("  Votre nom/pseudo : ").strip() or "trader"
        email = input("  Votre email : ").strip()
        return {"type": "trial", "name": name, "email": email}


def step_firm():
    """Etape 2 : Prop firm."""
    print()
    print("  ── ETAPE 2 : Prop Firm ──")
    print()
    firms_list = list(FIRMS.items())
    for i, (key, info) in enumerate(firms_list, 1):
        print(f"    {i}. {info['name']}")

    choice = input(f"\n  Choix (1-{len(firms_list)}) : ").strip()
    idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(firms_list) else 0
    firm_key = firms_list[idx][0]
    firm_info = firms_list[idx][1]
    print(f"  -> {firm_info['name']}")
    return firm_key


def step_plan(firm_key):
    """Etape 3 : Plan."""
    print()
    print("  ── ETAPE 3 : Plan ──")
    print()
    plans = FIRMS[firm_key]["plans"]
    plans_list = list(plans.items())
    for i, (key, desc) in enumerate(plans_list, 1):
        print(f"    {i}. {desc}")

    choice = input(f"\n  Choix (1-{len(plans_list)}) : ").strip()
    idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(plans_list) else 0
    plan_key = plans_list[idx][0]
    print(f"  -> {plans_list[idx][1]}")
    return plan_key


def step_instrument():
    """Etape 4 : Instrument."""
    print()
    print("  ── ETAPE 4 : Instrument ──")
    print()
    instr_list = list(INSTRUMENTS.items())
    for i, (sym, desc) in enumerate(instr_list, 1):
        print(f"    {i:2d}. {desc}")

    choice = input(f"\n  Choix (1-{len(instr_list)}) [1=MNQ] : ").strip()
    idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(instr_list) else 0
    sym = instr_list[idx][0]
    print(f"  -> {sym}")
    return sym


def step_copy_accounts():
    """Etape 5 : Comptes copies (optionnel)."""
    print()
    print("  ── ETAPE 5 : Copy Trading (optionnel) ──")
    print("  Voulez-vous copier les trades sur d'autres comptes ?")
    print("  (Chaque compte copie necessite sa propre licence)")
    print()

    choice = input("  Copier des comptes ? (o/N) : ").strip().lower()
    if choice not in ('o', 'y', 'oui', 'yes'):
        return []

    accounts = []
    while True:
        acc_id = input("  ID du compte a copier (vide pour finir) : ").strip()
        if not acc_id:
            break
        accounts.append(acc_id)
    return accounts


def save_config(license_info, firm, plan, instrument, trader_name, copy_accounts):
    """Sauvegarde la config dans .env."""
    env_path = BASE_DIR / '.env'
    env_vars = {}

    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                env_vars[k.strip()] = v.strip()

    env_vars['PROP_FIRM'] = firm
    env_vars['TOPSTEP_ACCOUNT_TYPE'] = plan
    env_vars['INSTRUMENT'] = instrument
    env_vars['TRADER_ID'] = trader_name
    env_vars['TRADING_PORT'] = env_vars.get('TRADING_PORT', '8001')

    if 'RISK_DESK_ADMIN_KEY' not in env_vars:
        env_vars['RISK_DESK_ADMIN_KEY'] = secrets.token_urlsafe(16)

    if license_info.get('key'):
        env_vars['LICENSE_KEY'] = license_info['key']

    lines = [f"{k}={v}" for k, v in env_vars.items()]
    env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    return env_vars


def print_summary(env_vars, license_info):
    """Affiche le resume."""
    port = env_vars.get('TRADING_PORT', '8001')
    admin_key = env_vars.get('RISK_DESK_ADMIN_KEY', '')

    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║        x-trade.ai — Pret !               ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print(f"  Prop Firm  : {FIRMS.get(env_vars.get('PROP_FIRM', ''), {}).get('name', '?')}")
    print(f"  Plan       : {env_vars.get('TOPSTEP_ACCOUNT_TYPE', '?')}")
    print(f"  Instrument : {env_vars.get('INSTRUMENT', '?')}")
    print(f"  Trader     : {env_vars.get('TRADER_ID', '?')}")

    if license_info.get('key'):
        print(f"  Licence    : {license_info['key']}")

    print()
    print(f"  VOTRE DASHBOARD :")
    print(f"    http://localhost:{port}/risk-desk")
    print()
    print(f"  ADMIN (ne pas partager) :")
    print(f"    http://localhost:{port}/risk-desk/admin?admin_key={admin_key}")
    print()


def main():
    header()
    ensure_deps()

    # Etape 1 : Licence
    license_info = step_license()
    trader_name = license_info.get('name', license_info.get('key', 'trader')[:8])

    # Etape 2 : Prop firm
    firm = step_firm()

    # Etape 3 : Plan
    plan = step_plan(firm)

    # Etape 4 : Instrument
    instrument = step_instrument()

    # Etape 5 : Copy accounts
    copy_accounts = step_copy_accounts()

    # Sauvegarde
    env_vars = save_config(license_info, firm, plan, instrument, trader_name, copy_accounts)

    # Resume
    print_summary(env_vars, license_info)

    # Demarrer
    answer = input("  Demarrer x-trade.ai maintenant ? (O/n) : ").strip().lower()
    if answer in ('', 'o', 'y', 'oui', 'yes'):
        print()
        print("  Demarrage du Risk Desk...")
        print()
        os.system(f'{sys.executable} main.py')
    else:
        print()
        print("  Pour demarrer plus tard :")
        print("    python main.py")
        print("    ou double-cliquez sur start_risk_desk.bat")
        print()


if __name__ == '__main__':
    main()
