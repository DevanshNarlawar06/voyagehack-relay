"""`relay` CLI (Typer): validate, simulate, policy test, audit verify, kill, new.

WP1 provides the app and the `db` / `config` groups; later WPs add their own command groups by
registering a Typer sub-app here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from relay import __version__

app = typer.Typer(no_args_is_help=True, help="Relay: governed HR workflow runtime.")
db_app = typer.Typer(no_args_is_help=True, help="Database migrations.")
config_app = typer.Typer(no_args_is_help=True, help="Configuration.")
app.add_typer(db_app, name="db")
policy_app = typer.Typer(no_args_is_help=True, help="Policy packs: test and activate.")
app.add_typer(config_app, name="config")
audit_app = typer.Typer(no_args_is_help=True, help="Audit log.")
app.add_typer(audit_app, name="audit")
app.add_typer(policy_app, name="policy")

# Policy text contains ₹ and other non-ASCII; Windows consoles default to cp1252.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


@app.command()
def version() -> None:
    """Print the Relay version."""
    typer.echo(__version__)


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Apply migrations up to REVISION (default: head)."""
    from relay.db.migrate import upgrade

    upgrade(revision)
    typer.echo(f"database upgraded to {revision}")


@audit_app.command("verify")
def audit_verify(url: str | None = None) -> None:
    """Recompute the audit hash chain. Exits 1 at the first break."""
    from relay.core.audit.verifier import verify_chain
    from relay.db.session import session_scope

    with session_scope(url) as session:
        result = verify_chain(session)
    if not result.ok:
        typer.echo(f"audit chain BROKEN at seq {result.first_bad_seq}: {result.problem}")
        raise typer.Exit(code=1)
    typer.echo(f"audit chain ok: {result.checked} events, head {result.head_hash}")


@config_app.command("check")
def config_check() -> None:
    """Validate every file in config/."""
    from relay.config import load_config

    cfg = load_config()
    typer.echo(f"config ok: tier1={cfg.models.tiers['tier1']} tier2={cfg.models.tiers['tier2']}")


@config_app.command("gen-key")
def config_gen_key() -> None:
    """Print a fresh RELAY_ENCRYPTION_KEY."""
    from relay.db.crypto import generate_key

    typer.echo(generate_key())


@policy_app.command("test")
def policy_test(
    pack: str | None = typer.Argument(None, help="Only run cases for this pack."),
    draft: Path | None = typer.Option(None, help="Draft pack YAML to test in place of the file."),
) -> None:
    """Run definitions/policies/tests/*.yaml against the packs on disk (no database needed)."""
    from relay.core.policy.packs import load_dir, parse_pack
    from relay.core.policy.service import DEFAULT_POLICY_DIR
    from relay.core.policy.testing import load_case_files, run_cases

    policies, _ = load_dir(DEFAULT_POLICY_DIR)
    if draft is not None:
        policies = policies.with_pack(parse_pack(draft.read_text(encoding="utf-8")))
    results = run_cases(policies, load_case_files(DEFAULT_POLICY_DIR / "tests"), pack)
    for r in results:
        typer.echo(f"{'PASS' if r.passed else 'FAIL'}  {r.pack}: {r.name}  ({r.detail})")
    failed = sum(not r.passed for r in results)
    typer.echo(f"{len(results) - failed} passed, {failed} failed  [{policies.version}]")
    raise typer.Exit(1 if failed else 0)


@policy_app.command("activate")
def policy_activate(
    file: Path,
    actor: str = typer.Option(..., help="Who is activating (an hr_admin user id)."),
    tests: Path | None = typer.Option(None, help="Edited test case file for this pack."),
) -> None:
    """Test and activate a pack. It applies to the next authorize() call, with no restart."""
    from relay.core.policy import PolicyService, PolicyTestFailure
    from relay.db.session import session_scope

    try:
        with session_scope() as session:
            row = PolicyService().activate(
                session,
                file.read_text(encoding="utf-8"),
                actor=actor,
                draft_tests=tests.read_text(encoding="utf-8") if tests else None,
            )
            typer.echo(f"activated {row.pack}@{row.version}")
    except (PolicyTestFailure, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def kill(
    scope: str = typer.Option(..., help="global | workflow | step | skill | connector"),
    target: str = typer.Option("*", help="Name of the workflow/step/skill/connector."),
    off: bool = typer.Option(False, "--off", help="Release the switch instead."),
    actor: str = typer.Option(..., help="Who is setting the switch (an hr_admin user id)."),
    note: str | None = typer.Option(None),
) -> None:
    """Set or release a kill switch. Takes effect within one second (PRD 12.6)."""
    from relay.core.policy import PolicyService
    from relay.db.session import session_scope

    with session_scope() as session:
        PolicyService().set_kill_switch(
            session, scope, target, active=not off, actor=actor, note=note
        )
    typer.echo(f"kill switch {scope}:{target} {'released' if off else 'ACTIVE'}")


seed_app = typer.Typer(no_args_is_help=True, help="Synthetic dataset (seed/fixtures).")
app.add_typer(seed_app, name="seed")


@seed_app.command("generate")
def seed_generate() -> None:
    """Regenerate seed/fixtures/*.json deterministically."""
    from relay.seeding.generate import generate, write

    for path in write(generate()):
        typer.echo(f"wrote {path.name}")


@seed_app.command("load")
def seed_load() -> None:
    """Load seed/fixtures into the database (idempotent)."""
    from relay.db.session import session_scope
    from relay.seeding.generate import read
    from relay.seeding.load import load

    with session_scope() as session:
        report = load(session, read())
    typer.echo(
        f"users={report.users} employees={report.employees} candidates={report.candidates} "
        f"approved_offers={report.approved_offers}"
    )
