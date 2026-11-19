"""
cloud-boost structured logger
JSON-structured logging — no string concat, no PII leaks
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from colorama import Fore, Style, init

init(autoreset=True)

# ─── Color Map ────────────────────────────────────────────────────────────────
LEVEL_COLORS = {
    "DEBUG": Fore.WHITE,
    "INFO": Fore.CYAN,
    "WARNING": Fore.YELLOW,
    "ERROR": Fore.RED,
    "CRITICAL": Fore.RED + Style.BRIGHT,
    "SUCCESS": Fore.GREEN,
}

LEVEL_ICONS = {
    "DEBUG": "·",
    "INFO": "→",
    "WARNING": "⚠",
    "ERROR": "✗",
    "CRITICAL": "☠",
    "SUCCESS": "✓",
}


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter for file output."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            entry["data"] = record.extra_data
        return json.dumps(entry)


class ConsoleFormatter(logging.Formatter):
    """Pretty console output with colors and icons."""

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        color = LEVEL_COLORS.get(level, "")
        icon = LEVEL_ICONS.get(level, "→")
        return f"{color}{icon} {record.getMessage()}{Style.RESET_ALL}"


def get_logger(
    name: str = "cloudboost",
    log_file: str = "",
    verbose: bool = False,
) -> logging.Logger:
    """Create and return a configured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)

    # File handler (structured JSON)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(StructuredFormatter())
        logger.addHandler(file_handler)

    return logger


def log_with_data(logger: logging.Logger, level: str, message: str, **data) -> None:
    """Log a message with structured extra data."""
    record = logger.makeRecord(
        logger.name, getattr(logging, level.upper()), "", 0, message, (), None
    )
    record.extra_data = data
    logger.handle(record)
