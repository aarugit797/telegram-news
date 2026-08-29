import logging
import json
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """
    Converts each Python log record into a single line of JSON,
    instead of the logging module's default plain-text layout. This
    is what makes our logs machine-queryable once they reach
    CloudWatch on AWS - CloudWatch Logs Insights can run structured
    field queries (e.g. filter component="github_agent") directly
    against JSON, which it cannot do against an unstructured text
    line like a print() statement produces.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "component": record.name,   # e.g. "app.agents.github_agent" - set automatically from __name__
            "message": record.getMessage(),
        }

        # Lets callers attach structured context specific to what
        # they're logging, e.g.:
        #   logger.info("Signal approved", extra={"extra_fields":
        #       {"signal_id": str(signal.id), "source": "github",
        #        "composite_score": 4.3}})
        # without us having to predefine every possible field here
        # in advance.
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def get_logger(name: str) -> logging.Logger:
    """
    Every file in the system calls this instead of using print().

    Usage:  logger = get_logger(__name__)

    Passing __name__ means the logger is automatically tagged with
    exactly which module produced each line - e.g.
    "app.agents.github_agent" vs "app.responder.webhook" - essential
    once 5 agents and the responder are all running and writing to
    the same stdout stream concurrently.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        # Guards against re-adding a handler if get_logger(__name__)
        # happens to be called more than once for the same name -
        # every file that imports this calls it once at import time,
        # and Python module caching means that import only truly
        # runs once, but this guard makes the function safe
        # regardless.
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  
    return logger
