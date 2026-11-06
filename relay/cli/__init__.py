"""`relay` CLI (Typer): validate, simulate, policy test, audit verify, kill, new.

WP1 provides the app and the `db` / `config` groups; later WPs add their own command groups by
registering a Typer sub-app here.
"""

from __future__ import annotations

import typer

from relay import __version__

app = typer.Typer(no_args_is_help=True, help="Relay: governed HR workflow runtime.")
db_app = typer.Typer(no_args_is_help=True, help="Database migrations.")
config_app = typer.Typer(no_args_is_help=True, help="Configuration.")
app.add_typer(db_app, name="db")
app.add_typer(config_app, name="config")


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
