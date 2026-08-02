"""Central logging configuration."""

import logging


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return the MaxiCrawler root logger.

    Existing handlers are preserved so embedding applications retain ownership
    of their logging setup.
    """
    logger = logging.getLogger("maxicrawler")
    logger.setLevel(level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return logger
