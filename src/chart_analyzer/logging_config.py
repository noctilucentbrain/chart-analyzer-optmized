from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_level: str = "INFO", log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_logging_error = None
    if log_file:
        path = Path(log_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(path, encoding="utf-8"))
        except OSError as error:
            file_logging_error = error

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )

    if file_logging_error is not None:
        logging.getLogger(__name__).warning(
            "File logging disabled for %s: %s",
            log_file,
            file_logging_error,
        )
