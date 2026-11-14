"""Rotating operational logs; never intentionally include member message content."""

import logging
from logging.handlers import RotatingFileHandler

from aegis.config import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self.secrets = tuple(s for s in secrets if s)

    def format(self, record):
        output = super().format(record)
        for secret in self.secrets:
            output = output.replace(secret, "[REDACTED]")
        return output


def configure(settings: Settings):
    formatter = RedactingFormatter((settings.token, settings.api_key))
    handlers = [logging.StreamHandler()]
    if settings.log_file:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                settings.log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=settings.log_level, handlers=handlers, force=True)
    # HTTP debug output can contain payloads. Do not inherit LOG_LEVEL=DEBUG here.
    for name in ("discord.http", "discord.gateway", "httpx", "httpcore", "openai", "aiosqlite"):
        logging.getLogger(name).setLevel(logging.WARNING)
