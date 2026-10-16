"""RingLens command-line entry point."""

import json
import logging
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError

from ringlens.config import Settings
from ringlens.dataset_io import load_dataset, read_batch
from ringlens.graph_store import GraphStore, GraphStoreError
from ringlens.ingest import ingest_batch
from ringlens.investigation_models import InvestigationConfig
from ringlens.investigator import (
    InvestigationDataUnavailable,
    InvestigationInputError,
    investigate,
    render_text,
)
from ringlens.schema import initialize_schema
from ringlens.synthetic import generate_demo, parse_as_of, write_demo
from ringlens.verify import VerificationReport, graph_stats, path_json, sample_paths, verify_graph

app = typer.Typer(help="RingLens: graph-backed refund abuse investigations.", no_args_is_help=True)
schema_app = typer.Typer(help="Manage the fixed RingLens graph schema.", no_args_is_help=True)
app.add_typer(schema_app, name="schema")
data_app = typer.Typer(help="Generate and validate synthetic fixture files.", no_args_is_help=True)
app.add_typer(data_app, name="data")
graph_app = typer.Typer(
    help="Verify stored graph truth and inspect sample paths.", no_args_is_help=True
)
app.add_typer(graph_app, name="graph")


@app.callback()
def main() -> None:
    """Operate the RingLens project foundation."""


def _load_settings(graph_name: str | None = None) -> Settings:
    try:
        settings = Settings(graph_name=graph_name) if graph_name is not None else Settings()
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
def schema_init(graph: Annotated[str | None, typer.Option("--graph")] = None) -> None:
    """Create missing indexes without seeding or deleting application data."""
    settings = _load_settings(graph)
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


@app.command("ingest")
def ingest_command(
    input_directory: Annotated[Path, typer.Option("--input")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    graph: Annotated[str | None, typer.Option("--graph")] = None,
) -> None:
    """Validate and import a base/append batch; dry-run creates no graph state."""
    try:
        batch = read_batch(input_directory)
        settings = _load_settings(graph)
        with GraphStore(settings) as store:
            result = ingest_batch(store, batch, dry_run=dry_run)
    except (ValueError, OSError, GraphStoreError) as error:
        typer.echo(f"Ingestion failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(result.model_dump_json(indent=2))


@graph_app.command("verify")
def verify_command(graph: Annotated[str | None, typer.Option("--graph")] = None) -> None:
    """Return a structured integrity/readiness report; failures exit nonzero."""
    settings = _load_settings(graph)
    try:
        with GraphStore(settings) as store:
            report = verify_graph(store)
    except (ValueError, GraphStoreError) as error:
        report = VerificationReport(issues=[f"Graph verification failed: {error}"])
    typer.echo(report.model_dump_json(indent=2))
    if not report.ok:
        raise typer.Exit(code=1)


@graph_app.command("stats")
def stats_command(graph: Annotated[str | None, typer.Option("--graph")] = None) -> None:
    """Return node/distinct-ID counts and relationship counts."""
    settings = _load_settings(graph)
    try:
        with GraphStore(settings) as store:
            result = graph_stats(store)
    except (ValueError, GraphStoreError) as error:
        typer.echo(f"Graph stats failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(result, indent=2))


@graph_app.command("paths")
def paths_command(
    refund: Annotated[str | None, typer.Option("--refund")] = None,
    account: Annotated[str | None, typer.Option("--account")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=20)] = 5,
    graph: Annotated[str | None, typer.Option("--graph")] = None,
) -> None:
    """Inspect real refund-context and bounded account/device/peer paths."""
    settings = _load_settings(graph)
    try:
        with GraphStore(settings) as store:
            result = sample_paths(store, refund_id=refund, account_id=account, limit=limit)
        for kind in ("refund_paths", "peer_paths"):
            result[kind] = [[path_json(path) for path in row] for row in result[kind]]
    except (ValueError, GraphStoreError) as error:
        typer.echo(f"Graph paths failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(result, indent=2))


@app.command("investigate")
def investigate_command(
    refund_id: Annotated[str, typer.Option("--refund-id")],
    window_days: Annotated[int, typer.Option("--window-days", min=1, max=3650)] = 30,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Whole-second UTC ISO timestamp.")
    ] = None,
    output_format: Annotated[Literal["text", "json"], typer.Option("--format")] = "text",
    graph: Annotated[str | None, typer.Option("--graph")] = None,
    max_accounts: Annotated[int, typer.Option("--max-accounts", min=1, max=1000)] = 100,
    max_evidence: Annotated[int, typer.Option("--max-evidence", min=1, max=100_000)] = 2000,
) -> None:
    """Investigate a refund through actual graph paths; create no cases or reviews."""
    try:
        assessment = parse_as_of(as_of) if as_of is not None else None
        settings = _load_settings(graph)
        with GraphStore(settings) as store:
            result = investigate(
                store,
                refund_id,
                as_of=assessment,
                config=InvestigationConfig(
                    window_days=window_days, max_accounts=max_accounts, max_evidence=max_evidence
                ),
            )
    except (ValueError, GraphStoreError) as error:
        invalid = isinstance(error, InvestigationInputError) or (
            isinstance(error, ValueError) and not isinstance(error, InvestigationDataUnavailable)
        )
        kind = "INVALID_INPUT" if invalid else "DATA_UNAVAILABLE"
        if output_format == "json":
            typer.echo(json.dumps({"error": {"kind": kind, "message": str(error)}}))
        else:
            typer.echo(f"Investigation failed [{kind}]: {error}", err=True)
        raise typer.Exit(code=2 if invalid else 1) from None
    typer.echo(result.model_dump_json(indent=2) if output_format == "json" else render_text(result))
    if result.completeness != "COMPLETE":
        raise typer.Exit(code=3)
