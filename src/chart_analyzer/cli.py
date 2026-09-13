from __future__ import annotations

from pathlib import Path

import click

from chart_analyzer.app import initialize_database
from chart_analyzer.app import run as run_app
from chart_analyzer.app import run_latest as run_latest_app
from chart_analyzer.app import run_service as run_service_app
from chart_analyzer.config import ConfigError, load_config
from chart_analyzer.logging_config import configure_logging


@click.group()
def cli() -> None:
    """Analyze market chart data and store candles plus indicators in MongoDB."""


@cli.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the YAML configuration file.",
)
@click.option("--log-level", default="INFO", show_default=True, help="Python logging level.")
def run(config_path: Path, log_level: str) -> None:
    """Run one batch ingestion and analysis pass."""
    try:
        config = load_config(config_path)
        configure_logging(log_level=log_level, log_file=config.service.log_file)
        run_app(config)
    except ConfigError as error:
        raise click.ClickException(str(error)) from error


@cli.command("init-db")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the YAML configuration file.",
)
@click.option("--log-level", default="INFO", show_default=True, help="Python logging level.")
def init_db(config_path: Path, log_level: str) -> None:
    """Initialize MongoDB with full configured history and events."""
    try:
        config = load_config(config_path)
        configure_logging(log_level=log_level, log_file=config.service.log_file)
        initialize_database(config)
    except ConfigError as error:
        raise click.ClickException(str(error)) from error


@cli.command("run-latest")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the YAML configuration file.",
)
@click.option("--log-level", default="INFO", show_default=True, help="Python logging level.")
def run_latest(config_path: Path, log_level: str) -> None:
    """Fetch and store only candles not yet present in MongoDB."""
    try:
        config = load_config(config_path)
        configure_logging(log_level=log_level, log_file=config.service.log_file)
        run_latest_app(config)
    except ConfigError as error:
        raise click.ClickException(str(error)) from error


@cli.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the YAML configuration file.",
)
@click.option(
    "--interval-seconds",
    type=float,
    default=None,
    help="Override service.poll_interval_seconds from the config.",
)
@click.option(
    "--event-interval-seconds",
    type=float,
    default=None,
    help="Override service.event_detection_interval_seconds from the config.",
)
@click.option("--log-level", default="INFO", show_default=True, help="Python logging level.")
def service(
    config_path: Path,
    interval_seconds: float | None,
    event_interval_seconds: float | None,
    log_level: str,
) -> None:
    """Run latest-only ingestion continuously as a polling service."""
    try:
        config = load_config(config_path)
        configure_logging(log_level=log_level, log_file=config.service.log_file)
        run_service_app(
            config,
            interval_seconds=interval_seconds,
            event_interval_seconds=event_interval_seconds,
        )
    except ConfigError as error:
        raise click.ClickException(str(error)) from error
    except ValueError as error:
        raise click.ClickException(str(error)) from error


@cli.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the YAML configuration file.",
)
@click.option("--host", default="0.0.0.0", show_default=True, help="API host.")
@click.option("--port", default=8000, show_default=True, type=int, help="API port.")
@click.option("--log-level", default="INFO", show_default=True, help="Python logging level.")
def api(config_path: Path, host: str, port: int, log_level: str) -> None:
    """Run the REST API for service control and data access."""
    try:
        import uvicorn

        from chart_analyzer.api import create_app

        app = create_app(config_path, log_level=log_level)
        uvicorn.run(app, host=host, port=port, log_level=log_level.lower())
    except ConfigError as error:
        raise click.ClickException(str(error)) from error
