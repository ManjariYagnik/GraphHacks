"""RingLens command-line entry point."""

import logging

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError

from ringlens.config import Settings
from ringlens.graph_store import GraphStore, GraphStoreError

app = typer.Typer(help="RingLens: graph-backed refund abuse investigations.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Operate the RingLens project foundation."""


@app.command()
def doctor() -> None:
    """Check configuration, connectivity, and FalkorDB without creating a graph."""
    try:
        settings = Settings()
    except ValidationError as error:
        typer.echo(
            "Configuration invalid; fix these variables in .env or the environment:", err=True
        )
        for issue in error.errors(include_input=False, include_context=False, include_url=False):
            name = "RINGLENS_" + str(issue["loc"][0]).upper()
            typer.echo(f"  {name}: {issue['msg']}", err=True)
        raise typer.Exit(code=2) from None
    except (SettingsError, OSError, UnicodeError):
        typer.echo(
            "Cannot load configuration; check .env encoding, permissions, and syntax.", err=True
        )
        raise typer.Exit(code=2) from None

    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    try:
        with GraphStore(settings) as store:
            store.check_health()
    except GraphStoreError as error:
        typer.echo(f"Doctor failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo("Doctor OK: configuration valid, database reachable, FalkorDB commands available.")
