from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from .config import Settings
from .db import build_engine, build_session_factory
from .idempotency import ensure_namespace, ensure_verified_namespace
from .models import ROLE_NAMES, Role, User, WalletAuthorization
from .security import hash_password
from .chain import LocalChainGateway, ChainUnavailable
from .worker import ChainIndexer, ChainWorker


SEED_SPECS = (
    ("foundation", "Foundation", "foundation", "POG_SEED_FOUNDATION"),
    ("recipient", "Recipient", "recipient", "POG_SEED_RECIPIENT"),
    ("donor", "Donor", "donor", "POG_SEED_DONOR"),
    ("admin", "Administrator / Human Approver", "human_approver", "POG_SEED_ADMIN"),
)

LEGACY_RENAMES = (
    ("foundation-demo", "foundation", "Foundation"),
    ("recipient-demo", "recipient", "Recipient"),
    ("donor-demo", "donor", "Donor"),
    ("human-demo", "admin", "Administrator / Human Approver"),
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
        if prefix == "POG_SEED_ADMIN" and not password and not wallet:
            # Compatibility input for A1 operators; the created standard username is still admin.
            password = os.getenv("POG_SEED_HUMAN_PASSWORD", "")
            wallet = os.getenv("POG_SEED_HUMAN_WALLET", "")
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
        for old_name, new_name, _display in LEGACY_RENAMES:
            if session.scalar(select(User).where(User.username == old_name)) is not None and session.scalar(
                select(User).where(User.username == new_name)
            ) is None:
                raise RuntimeError(
                    "Legacy demo users exist; run migrate-standard-usernames explicitly before seeding"
                )
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


def migrate_standard_usernames(confirm: bool) -> int:
    if not confirm:
        print("Refusing rename without --confirm-standard-usernames", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            names = [value for pair in LEGACY_RENAMES for value in pair[:2]]
            users = session.scalars(
                select(User).where(User.username.in_(names)).with_for_update()
            ).all()
            by_name = {user.username: user for user in users}
            conflicts = [
                f"{old}->{new}" for old, new, _display in LEGACY_RENAMES
                if old in by_name and new in by_name
            ]
            if conflicts:
                raise RuntimeError("Username migration conflict; no changes applied: " + ", ".join(conflicts))
            changed = 0
            for old, new, display in LEGACY_RENAMES:
                user = by_name.get(old)
                if user is not None:
                    user.username = new
                    user.display_name = display
                    changed += 1
        print(f"Standard username migration complete; renamed={changed}; existing targets unchanged.")
        return 0
    finally:
        engine.dispose()


def seed_chain_demo(confirm: bool) -> int:
    if not confirm:
        print("Refusing to seed without --confirm-chain-demo-fixtures", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    gateway = _gateway(settings)
    specs = (
        ("foundation", "Foundation", "foundation", "POG_SEED_FOUNDATION", "foundation"),
        ("recipient", "Recipient", "recipient", "POG_SEED_RECIPIENT", "recipient"),
        ("donor", "Donor", "donor", "POG_SEED_DONOR", "donorA"),
        ("admin", "Administrator / Human Approver", "human_approver", "POG_SEED_ADMIN", "humanApprover"),
        ("service-ai-fixture", "Synthetic AI PRE fixture", "service_ai", "POG_SEED_AI_FIXTURE", "aiSigner"),
        ("donor-fixture-b", "Second Donor test fixture", "donor", "POG_SEED_DONOR_B", "donorB"),
    )
    passwords: dict[str, str] = {}
    for username, _display, _role, prefix, _manifest_role in specs:
        password = os.getenv(prefix + "_PASSWORD", "")
        if len(password) < 12 or "change_me" in password.lower() or "placeholder" in password.lower():
            print(f"Missing secure {prefix}_PASSWORD for {username}", file=sys.stderr)
            return 2
        passwords[username] = password
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            ensure_verified_namespace(session, gateway)
            for role_name in ROLE_NAMES:
                if session.get(Role, role_name) is None:
                    session.add(Role(name=role_name))
            session.flush()
            for old_name, new_name, _display in LEGACY_RENAMES:
                if session.scalar(select(User).where(User.username == old_name)) is not None:
                    raise RuntimeError(
                        f"Legacy {old_name} exists; run the explicit standard username migration first"
                    )
            for username, display, role_name, _prefix, manifest_role in specs:
                wallet = gateway.roles[manifest_role].lower()
                user = session.scalar(select(User).where(User.username == username))
                if user is None:
                    user = User(
                        username=username, display_name=display,
                        password_hash=hash_password(passwords[username]), active=True,
                    )
                    session.add(user)
                    session.flush()
                    session.add(WalletAuthorization(
                        user_id=user.id, role_name=role_name, wallet_address=wallet, active=True,
                    ))
                    continue
                mappings = session.scalars(select(WalletAuthorization).where(
                    WalletAuthorization.user_id == user.id,
                    WalletAuthorization.active.is_(True),
                )).all()
                if len(mappings) != 1 or mappings[0].role_name != role_name or mappings[0].wallet_address.lower() != wallet:
                    raise RuntimeError(
                        f"Existing {username} mapping differs; chain seed never rewrites role/wallet"
                    )
        print("A2 chain demo identities ensured; existing passwords and mappings were not modified.")
        return 0
    finally:
        engine.dispose()


def _gateway(settings: Settings) -> LocalChainGateway:
    if not settings.chain_enabled or settings.chain_manifest is None:
        raise RuntimeError("A2 chain mode is disabled")
    return LocalChainGateway(settings.chain_manifest, Path(__file__).resolve().parents[4])


def run_chain_service(kind: str, once: bool, interval: float, rebuild: bool = False) -> int:
    settings = Settings.from_env()
    gateway = _gateway(settings)
    if kind == "payment":
        if not settings.full_demo_enabled:
            raise RuntimeError("Opt-in full simulation is disabled")
        from .payment_worker import PaymentWorker
        service = PaymentWorker(settings.database_url, gateway)
    else:
        service = ChainWorker(settings.database_url, gateway) if kind == "worker" else ChainIndexer(
            settings.database_url, gateway
        )
    try:
        if rebuild:
            if kind != "indexer":
                raise RuntimeError("Only chain-indexer supports --rebuild")
            service.rebuild()
            return 0
        if once:
            service.once()
            return 0
        while True:
            try:
                worked = service.once()
            except ChainUnavailable:
                # Outage is not evidence of a reorg or failed send; retry the
                # next bounded reconciliation tick, never assign a new nonce.
                worked = False
            if not worked:
                time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        service.close()


def provision_full_simulation(confirm: bool, cents: str) -> int:
    if not confirm:
        print("Refusing without --confirm-simulation-fixtures", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    if not settings.full_demo_enabled:
        raise RuntimeError("Opt-in full simulation is disabled")
    from .payment_domain import provision_simulation
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            gateway = _gateway(settings)
            ns = ensure_verified_namespace(session, gateway)
            result = provision_simulation(session, ns, gateway, cents)
        print(f"Simulation accounts provisioned: count={result['accountCount']}; no real deposits or payments.")
        return 0
    finally:
        engine.dispose()


def retry_funding_preflight_command(confirm: bool, operation_id: UUID) -> int:
    """Explicit maintenance retry, never an unknown-broadcast recovery or top-up."""
    if not confirm:
        print("Refusing without --confirm-retry; no funding state was changed", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    if not settings.full_demo_enabled:
        raise RuntimeError("Opt-in full simulation is disabled")
    from .payment_recovery import retry_funding_preflight
    from .errors import APIError
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            gateway = _gateway(settings)
            ns = ensure_verified_namespace(session, gateway)
            result = retry_funding_preflight(session, ns, operation_id, gateway)
        import json
        print(json.dumps(result, sort_keys=True))
        return 0
    except APIError as exc:
        print("Funding preflight recovery refused: " + exc.code, file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="PoG API A2 administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed_parser = subparsers.add_parser("seed-demo")
    seed_parser.add_argument("--confirm-demo-fixtures", action="store_true")
    chain_seed_parser = subparsers.add_parser("seed-chain-demo")
    chain_seed_parser.add_argument("--confirm-chain-demo-fixtures", action="store_true")
    rename_parser = subparsers.add_parser("migrate-standard-usernames")
    rename_parser.add_argument("--confirm-standard-usernames", action="store_true")
    full_parser = subparsers.add_parser("provision-full-simulation")
    full_parser.add_argument("--confirm-simulation-fixtures", action="store_true")
    full_parser.add_argument("--donor-opening-hkd-cents", default="100000")
    retry_parser = subparsers.add_parser("retry-funding-preflight")
    retry_parser.add_argument("--operation-id", type=UUID, required=True)
    retry_parser.add_argument("--confirm-retry", action="store_true")
    for command in ("chain-worker", "chain-indexer", "payment-worker"):
        service_parser = subparsers.add_parser(command)
        service_parser.add_argument("--once", action="store_true")
        service_parser.add_argument("--interval", type=float, default=1.0)
        if command == "chain-indexer":
            service_parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    if args.command == "seed-demo":
        raise SystemExit(seed_demo(args.confirm_demo_fixtures))
    if args.command == "seed-chain-demo":
        raise SystemExit(seed_chain_demo(args.confirm_chain_demo_fixtures))
    if args.command == "migrate-standard-usernames":
        raise SystemExit(migrate_standard_usernames(args.confirm_standard_usernames))
    if args.command == "chain-worker":
        raise SystemExit(run_chain_service("worker", args.once, args.interval))
    if args.command == "payment-worker":
        raise SystemExit(run_chain_service("payment", args.once, args.interval))
    if args.command == "provision-full-simulation":
        raise SystemExit(provision_full_simulation(args.confirm_simulation_fixtures, args.donor_opening_hkd_cents))
    if args.command == "retry-funding-preflight":
        raise SystemExit(retry_funding_preflight_command(args.confirm_retry, args.operation_id))
    if args.command == "chain-indexer":
        raise SystemExit(run_chain_service("indexer", args.once, args.interval, args.rebuild))


if __name__ == "__main__":
    main()
