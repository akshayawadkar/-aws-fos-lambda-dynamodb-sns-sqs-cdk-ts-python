import json
from typing import Any


def log(level: str, message: str, **fields: Any) -> None:
    """One JSON line per entry, so CloudWatch Logs Insights can filter on correlationId."""
    print(json.dumps({"level": level, "message": message, **fields}, default=str))
