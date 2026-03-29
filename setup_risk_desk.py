"""
Risk Desk — Setup & Launch
============================
Configure et demarre le Risk Desk.

Usage:
    python setup_risk_desk.py          # Setup interactif
    python setup_risk_desk.py --start  # Demarre directement (skip setup)

Etapes:
1. Verifie les dependances Python
2. Configure le .env (prop firm, plan, instrument, cle admin)
3. Affiche les URLs d'acces
4. Demarre le serveur
"""

import os
import sys
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).parent

FIRMS = {
    "topstep": {"plans": ["50k", "100k", "150k"], "name": "TopstepX"},
    "apex": {"plans": ["25k", "50k", "100k", "150k"], "name": "Apex Trader Funding (4.0)"},
    "bulenox": {
        "plans": ["25k_opt1", "25k_opt2", "50k_opt1", "50k_opt2",
                  "100k_opt1", "100k_opt2", "150k_opt1", "150k_opt2",
                  "250k_opt1", "250k_opt2"],
        "name": "Bulenox",
    },
    "tradeify": {"plans": ["50k", "100k", "150k"], "name": "Tradeify Growth"},
    "tpt": {"plans": ["25k", "50k", "75k", "100k", "150k"], "name": "TakeProfitTrader"},
}

INSTRUMENTS = ["MNQ", "NQ", "MES", "ES", "YM", "RTY", "CL", "MCL", "GC", "MGC", "6E", "NG", "SI", "ZB"]


def check_dependencies():
    """Verifie et installe les dependances."""
    print("  Verification des dependances...")
    missing = []
    for pkg, import_name in [
        ("fastapi", "fastapi"),
        ("uvicorn[standard]", "uvicorn"),
        ("python-dotenv", "dotenv"),
        ("loguru", "loguru"),
        ("numpy", "numpy"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"  Installation: {', '.join(missing)}")
        os.system(f'{sys.executable} -m pip install -q {" ".join(missing)}')
    print("  OK\n")


def setup_env_interactive():
    """Configuration interactive du .env."""
    env_path = BASE_DIR / '.env'
    env_vars = {}

    # Charge l'existant
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                env_vars[k.strip()] = v.strip()

    print("=" * 55)
    print("  RISK DESK — Configuration")
    print("=" * 55)
    print()

    # 1. Prop firm
    print("  Prop firms disponibles:")
    for i, (key, info) in enumerate(FIRMS.items(), 1):
        print(f"    {i}. {info['name']} ({key})")
    current_firm = env_vars.get('PROP_FIRM', 'topstep')
    choice = input(f"\n  Choix [{current_firm}]: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(FIRMS):
        firm = list(FIRMS.keys())[int(choice) - 1]
    elif choice in FIRMS:
        firm = choice
    else:
        firm = current_firm
    env_vars['PROP_FIRM'] = firm
    print(f"  -> {FIRMS[firm]['name']}\n")

    # 2. Plan
    plans = FIRMS[firm]["plans"]
    print(f"  Plans {FIRMS[firm]['name']}:")
    for i, p in enumerate(plans, 1):
        print(f"    {i}. {p}")
    current_plan = env_vars.get('TOPSTEP_ACCOUNT_TYPE', plans[0])
    choice = input(f"\n  Choix [{current_plan}]: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(plans):
        plan = plans[int(choice) - 1]
    elif choice in plans:
        plan = choice
    else:
        plan = current_plan
    env_vars['TOPSTEP_ACCOUNT_TYPE'] = plan
    print(f"  -> {plan}\n")

    # 3. Instrument
    print(f"  Instruments: {', '.join(INSTRUMENTS)}")
    current_instr = env_vars.get('INSTRUMENT', 'MNQ')
    choice = input(f"  Instrument [{current_instr}]: ").strip().upper()
    instrument = choice if choice in INSTRUMENTS else current_instr
    env_vars['INSTRUMENT'] = instrument
    print(f"  -> {instrument}\n")

    # 4. Trader ID
    current_trader = env_vars.get('TRADER_ID', 'trader_1')
    choice = input(f"  Nom du trader [{current_trader}]: ").strip()
    trader = choice if choice else current_trader
    env_vars['TRADER_ID'] = trader
    print()

    # 5. Port
    current_port = env_vars.get('TRADING_PORT', '8001')
    choice = input(f"  Port [{current_port}]: ").strip()
    port = choice if choice else current_port
    env_vars['TRADING_PORT'] = port

    # 6. Admin key
    if 'RISK_DESK_ADMIN_KEY' not in env_vars:
        env_vars['RISK_DESK_ADMIN_KEY'] = secrets.token_urlsafe(16)

    # Sauvegarde
    lines = [f"{k}={v}" for k, v in env_vars.items()]
    env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    return env_vars


def print_summary(env_vars):
    """Affiche le resume final."""
    firm = env_vars.get('PROP_FIRM', 'topstep')
    plan = env_vars.get('TOPSTEP_ACCOUNT_TYPE', '50k')
    instrument = env_vars.get('INSTRUMENT', 'MNQ')
    trader = env_vars.get('TRADER_ID', 'trader_1')
    port = env_vars.get('TRADING_PORT', '8001')
    admin_key = env_vars.get('RISK_DESK_ADMIN_KEY', '')

    print()
    print("=" * 55)
    print("  RISK DESK — Pret")
    print("=" * 55)
    print(f"  Firm       : {FIRMS.get(firm, {}).get('name', firm)}")
    print(f"  Plan       : {plan}")
    print(f"  Instrument : {instrument}")
    print(f"  Trader     : {trader}")
    print()
    print(f"  TRADER : http://localhost:{port}/risk-desk")
    print(f"  ADMIN  : http://localhost:{port}/risk-desk/admin?admin_key={admin_key}")
    print()
    print(f"  Cle admin : {admin_key}")
    print("=" * 55)
    print()


def start_server():
    """Demarre le serveur."""
    print("  Demarrage du Risk Desk...\n")
    os.system(f'{sys.executable} main.py')


def main():
    print()
    print("  ╔══════════════════════════════════╗")
    print("  ║         RISK DESK SETUP          ║")
    print("  ╚══════════════════════════════════╝")
    print()

    # Mode direct
    if '--start' in sys.argv:
        check_dependencies()
        env_path = BASE_DIR / '.env'
        env_vars = {}
        if env_path.exists():
            for line in env_path.read_text(encoding='utf-8').splitlines():
                if '=' in line and not line.startswith('#'):
                    k, _, v = line.partition('=')
                    env_vars[k.strip()] = v.strip()
        print_summary(env_vars)
        start_server()
        return

    check_dependencies()
    env_vars = setup_env_interactive()
    print_summary(env_vars)

    answer = input("  Demarrer maintenant ? (O/n) : ").strip().lower()
    if answer in ('', 'o', 'y', 'oui', 'yes'):
        start_server()
    else:
        print("  Pour demarrer : python setup_risk_desk.py --start")
        print("  Ou directement : python main.py")
        print()


if __name__ == '__main__':
    main()
