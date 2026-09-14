import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler


class SensitiveDataFilter(logging.Filter):
    """
    Sanitizes log messages to ensure access tokens, client IDs, passwords,
    and authorization headers are never leaked into log output.
    """

    PATTERNS = [
        (re.compile(r"(Bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*", re.IGNORECASE), r"\1[REDACTED]"),
        (re.compile(r"(access_token=)[^\s&]+", re.IGNORECASE), r"\1[REDACTED]"),
        (re.compile(r"(DHAN_ACCESS_TOKEN=)[^\s&]+", re.IGNORECASE), r"\1[REDACTED]"),
        (re.compile(r"(password=)[^\s&]+", re.IGNORECASE), r"\1[REDACTED]"),
        (re.compile(r"(secret=)[^\s&]+", re.IGNORECASE), r"\1[REDACTED]"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, replacement in self.PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True


LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "trading.log")


def setup_logging(log_level: str = "INFO") -> None:
    """
    Configures root logger with:
    - Console (stdout) handler
    - Rotating file handler → logs/trading.log (read by /api/v1/logs/)
    - Sensitive data filter on both handlers
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    if root_logger.handlers:
        return  # Already configured

    sensitive_filter = SensitiveDataFilter()
    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    # --- Console handler ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    # --- Rotating file handler (10 MB × 3 backups) ---
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(sensitive_filter)
    root_logger.addHandler(file_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
