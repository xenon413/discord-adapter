import json
import logging
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
        }
        for key in ("subscriber_id", "guild_id", "event_type"):
            if hasattr(record, key):
                payload[key] = self._redact(str(getattr(record, key)))
        if record.exc_info:
            payload["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str, secrets: tuple[str, ...]) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter(secrets))
    logging.basicConfig(level=level, handlers=[handler], force=True)
