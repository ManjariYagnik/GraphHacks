"""RingLens command-line entry point."""

import logging
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError

from ringlens.config import Settings
from ringlens.dataset_io import load_dataset
from ringlens.graph_store import GraphStore, GraphStoreError
from ringlens.schema import initialize_schema
from ringlens.synthetic import generate_demo, parse_as_of, write_demo

app = typer.Typer(help="RingLens: graph-backed refund abuse investigations.", no_args_is_help=True)
schema_app = typer.Typer(help="Manage the fixed RingLens graph schema.", no_args_is_help=True)
app.add_typer(schema_app, name="schema")
data_app = typer.Typer(help="Generate and validate synthetic fixture files.", no_args_is_help=True)
app.add_typer(data_app, name="data")


@app.callback()
def main() -> None:
    """Operate the RingLens project foundation."""


def _load_settings() -> Settings:
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
    return settings


@app.command()
def doctor() -> None:
    """Check configuration, connectivity, and FalkorDB without creating a graph."""
    settings = _load_settings()
    try:
        with GraphStore(settings) as store:
            store.check_health()
    except GraphStoreError as error:
        typer.echo(f"Doctor failed: {error}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo("Doctor OK: configuration valid, database reachable, FalkorDB commands available.")


@schema_app.command("init")
def schema_init() -> None:
    """Create missing indexes without seeding or deleting application data."""
    settings = _load_settings()
    try:
        with GraphStore(settings) as store:
            store.check_health()
            result = initialize_schema(store)
    except GraphStoreError as error:
        typer.echo(f"Schema initialization failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"Schema ready: {result.created_indexes} indexes created, "
        f"{result.existing_indexes} already present. No records seeded. "
        "Indexes do not enforce uniqueness; the importer must validate it."
    )


@data_app.command("generate")
def data_generate(
    as_of: Annotated[
        str, typer.Option("--as-of", help="Whole-second UTC ISO timestamp; required.")
    ],
    seed: Annotated[int, typer.Option("--seed", min=0)] = 42,
    output: Annotated[Path, typer.Option("--output")] = Path("data/demo"),
    evaluation_output: Annotated[Path, typer.Option("--evaluation-output")] = Path(
        "evaluation/demo_ground_truth.json"
    ),
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace differing generated files.")
    ] = False,
) -> None:
    """Generate deterministic base and append fixtures without a database."""
    try:
        demo = generate_demo(seed=seed, as_of=parse_as_of(as_of))
        write_demo(demo, output=output, evaluation_output=evaluation_output, overwrite=overwrite)
    except (ValueError, OSError) as error:
        typer.echo(f"Data generation failed: {error}", err=True)
        raise typer.Exit(code=2) from None
    typer.echo("Generated base: 50 accounts, 42 devices, 46 addresses, 250 orders, 42 refunds.")
    typer.echo("Generated append: 6 refunds; assessment horizon advanced by 2 days and 1 hour.")
    typer.echo("Evaluation metadata written separately; no graph data was changed.")


@data_app.command("validate")
def data_validate(
    input_directory: Annotated[
        Path, typer.Option("--input", help="Batch directory containing manifest.json.")
    ],
    base_directory: Annotated[
        Path | None, typer.Option("--base", help="Base dataset for append references.")
    ] = None,
) -> None:
    """Validate canonical files, hashes, records, and references without a database."""
    try:
        batch = load_dataset(input_directory, base_directory=base_directory)
    except (ValueError, OSError) as error:
        typer.echo(f"Data validation failed: {error}", err=True)
        raise typer.Exit(code=2) from None
    counts = ", ".join(f"{file.count} {file.kind}" for file in batch.manifest.files)
    typer.echo(f"Data valid ({batch.manifest.mode}): {counts}.")
