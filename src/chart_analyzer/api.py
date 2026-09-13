from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse, Response
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from pydantic import BaseModel

from chart_analyzer.app import initialize_database, run_latest
from chart_analyzer.config import load_config
from chart_analyzer.logging_config import configure_logging
from chart_analyzer.service import ServiceController
from chart_analyzer.storage import MongoStorage


class ServiceStartRequest(BaseModel):
    interval_seconds: float | None = None
    event_interval_seconds: float | None = None


def create_app(config_path: str | Path, log_level: str = "INFO") -> FastAPI:
    config = load_config(config_path)
    configure_logging(log_level=log_level, log_file=config.service.log_file)
    controller = ServiceController(config)
    app = FastAPI(title="Chart Analyzer")

    @app.on_event("shutdown")
    def shutdown_service() -> None:
        controller.stop(timeout=5)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/doc", include_in_schema=False)
    def docs_redirect() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/service/status")
    def service_status() -> dict[str, bool]:
        return {"running": controller.status().running}

    @app.post("/service/start")
    def service_start(request: ServiceStartRequest | None = None) -> dict[str, bool]:
        request = request or ServiceStartRequest()
        status = controller.start(
            interval_seconds=request.interval_seconds,
            event_interval_seconds=request.event_interval_seconds,
        )
        return {"running": status.running}

    @app.post("/service/stop")
    def service_stop() -> dict[str, bool]:
        status = controller.stop()
        return {"running": status.running}

    @app.post("/database/init")
    def database_init() -> dict[str, str]:
        try:
            initialize_database(config)
        except ServerSelectionTimeoutError as error:
            raise HTTPException(
                status_code=503,
                detail=(
                    "MongoDB is not reachable. If running in Docker, "
                    "do not use localhost unless MongoDB is in the same container."
                ),
            ) from error
        except PyMongoError as error:
            raise HTTPException(status_code=503, detail=f"MongoDB error: {error}") from error
        return {"status": "completed"}

    @app.post("/latest/run")
    def latest_run() -> dict[str, Any]:
        try:
            summaries = run_latest(config)
        except ServerSelectionTimeoutError as error:
            raise HTTPException(
                status_code=503,
                detail=(
                    "MongoDB is not reachable. If running in Docker, "
                    "do not use localhost unless MongoDB is in the same container."
                ),
            ) from error
        except PyMongoError as error:
            raise HTTPException(status_code=503, detail=f"MongoDB error: {error}") from error
        return {"status": "completed", "summaries": [asdict(summary) for summary in summaries]}

    @app.get("/candles/{ticker}/{timeframe}/{timestamp}")
    def candle_with_indicators(
        ticker: str,
        timeframe: str,
        timestamp: str,
        indicators: list[str] | None = Query(default=None),
    ) -> dict[str, Any]:
        storage = MongoStorage(
            config.mongodb.uri,
            config.mongodb.database,
            server_selection_timeout_ms=config.mongodb.server_selection_timeout_ms,
        )
        try:
            try:
                document = storage.get_candle_with_indicators(
                    ticker=ticker,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    indicators=indicators,
                )
            except ServerSelectionTimeoutError as error:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "MongoDB is not reachable. If running in Docker, "
                        "do not use localhost unless MongoDB is in the same container."
                    ),
                ) from error
            except PyMongoError as error:
                raise HTTPException(status_code=503, detail=f"MongoDB error: {error}") from error
        finally:
            storage.close()

        if document is None:
            raise HTTPException(status_code=404, detail="Candle not found")
        return document

    @app.get("/timeframes/{timeframe}/latest")
    def latest_candles_with_events(
        timeframe: str,
        ticker: str | None = None,
        indicators: list[str] | None = Query(default=None),
    ) -> dict[str, Any]:
        storage = MongoStorage(
            config.mongodb.uri,
            config.mongodb.database,
            server_selection_timeout_ms=config.mongodb.server_selection_timeout_ms,
        )
        try:
            try:
                document = storage.get_latest_candles_with_events(
                    timeframe=timeframe,
                    ticker=ticker,
                    indicators=indicators,
                )
            except ServerSelectionTimeoutError as error:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "MongoDB is not reachable. If running in Docker, "
                        "do not use localhost unless MongoDB is in the same container."
                    ),
                ) from error
            except PyMongoError as error:
                raise HTTPException(status_code=503, detail=f"MongoDB error: {error}") from error
        finally:
            storage.close()

        if not document["candles"]:
            raise HTTPException(status_code=404, detail="No candles found for timeframe")
        return document

    return app
