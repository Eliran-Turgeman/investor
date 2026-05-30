from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(root: Path) -> logging.Logger:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("investor_toolkit")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_path = log_dir / "research.log"
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", None) != str(log_path):
            logger.removeHandler(handler)
            handler.close()
    existing_paths = {
        getattr(handler, "baseFilename", None)
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler)
    }
    if str(log_path) not in existing_paths:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    return logger


def close_logging() -> None:
    logger = logging.getLogger("investor_toolkit")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
