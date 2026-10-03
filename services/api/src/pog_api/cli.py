from __future__ import annotations

import argparse
import os
import re
import sys

from sqlalchemy import select

from .config import Settings
from .db import build_engine, build_session_factory
from .idempotency import ensure_namespace
from .models import ROLE_NAMES, Role, User, WalletAuthorization
from .security import hash_password


SEED_SPECS = (
    ("foundation-demo", "Foundation Demo", "foundation", "POG_SEED_FOUNDATION"),
    ("recipient-demo", "Recipient Demo", "recipient", "POG_SEED_RECIPIENT"),
    ("donor-demo", "Donor Demo", "donor", "POG_SEED_DONOR"),
    ("human-demo", "Human Approver Demo", "human_approver", "POG_SEED_HUMAN"),
)


def seed_demo(confirm: bool) -> int:
    if not confirm:
        print("Refusing to seed without --confirm-demo-fixtures", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    required: dict[str, tuple[str, str]] = {}
    seen_wallets: set[str] = set()
    for username, _display, _role, prefix in SEED_SPECS:
        password = os.getenv(prefix + "_PASSWORD", "")
        wallet = os.getenv(prefix + "_WALLET", "")
        normalized_wallet = wallet.lower()
        if (
            len(password) < 12
            or "change_me" in password.lower()
            or "placeholder" in password.lower()
            or not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet)
            or int(wallet, 16) == 0
            or normalized_wallet in seen_wallets
        ):
            print(f"Missing secure {prefix}_PASSWORD/WALLET for {username}", file=sys.stderr)
            return 2
        seen_wallets.add(normalized_wallet)
        required[username] = (password, normalized_wallet)
    with factory() as session, session.begin():
        ensure_namespace(session, settings.run_id, settings.instance_id)
        for role_name in ROLE_NAMES:
            if session.get(Role, role_name) is None:
                session.add(Role(name=role_name))
        session.flush()
        for username, display, role_name, _prefix in SEED_SPECS:
            password, wallet = required[username]
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(
                    username=username,
                    display_name=display,
                    password_hash=hash_password(password),
                    active=True,
                )
                session.add(user)
                session.flush()
                session.add(
                    WalletAuthorization(
                        user_id=user.id,
                        role_name=role_name,
                        wallet_address=wallet,
                        active=True,
                    )
                )
            else:
                existing = session.scalar(
                    select(WalletAuthorization).where(WalletAuthorization.user_id == user.id)
                )
                if (
                    existing is None
                    or existing.role_name != role_name
                    or existing.wallet_address.lower() != wallet.lower()
                ):
                    raise RuntimeError(
                        f"Existing {username} mapping differs; seed never rewrites role/wallet"
                    )
    engine.dispose()
    print("Demo fixtures ensured; existing passwords and mappings were not modified.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="PoG API A1 administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed_parser = subparsers.add_parser("seed-demo")
    seed_parser.add_argument("--confirm-demo-fixtures", action="store_true")
    args = parser.parse_args()
    if args.command == "seed-demo":
        raise SystemExit(seed_demo(args.confirm_demo_fixtures))


if __name__ == "__main__":
    main()
