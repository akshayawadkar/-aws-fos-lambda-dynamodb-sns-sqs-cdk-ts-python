import json
from collections.abc import Callable
from typing import Any

from .contracts import Message, parse_message
from .log import log


def parse_sqs_body(body: str) -> Message:
    """Commands arrive as the raw envelope; events arrive wrapped by EventBridge under `detail`."""
    parsed = json.loads(body)
    return parse_message(parsed.get("detail", parsed))


def sqs_handler(handle: Callable[[Message], None]) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """
    Processes each record independently and reports only the failures, so one bad
    message doesn't redeliver the batch. A failing message reaches the DLQ after
    the queue's maxReceiveCount.
    """

    def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
        failures = []
        for record in event["Records"]:
            try:
                handle(parse_sqs_body(record["body"]))
            except Exception as err:
                log(
                    "error",
                    "Message processing failed",
                    sqsMessageId=record["messageId"],
                    error=str(err),
                )
                failures.append({"itemIdentifier": record["messageId"]})
        return {"batchItemFailures": failures}

    return handler
