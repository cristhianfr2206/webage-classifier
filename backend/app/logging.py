import logging
from contextvars import ContextVar

from pythonjsonlogger.jsonlogger import JsonFormatter

request_id: ContextVar[str] = ContextVar("request_id", default="-")


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(
        JsonFormatter(  # type: ignore[no-untyped-call]
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s"
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
