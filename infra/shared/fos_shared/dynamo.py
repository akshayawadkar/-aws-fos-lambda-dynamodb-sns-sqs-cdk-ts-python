import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError

from contracts import COMMAND_QUEUE_ENV, Message, to_wire

TransactItem = dict[str, Any]

SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60


@lru_cache(maxsize=1)
def client() -> Any:
    """A client that converts between Python values and DynamoDB's attribute format."""
    return boto3.resource("dynamodb").meta.client


def table_name() -> str:
    name = os.environ.get("TABLE_NAME")
    if not name:
        raise RuntimeError("TABLE_NAME is not set")
    return name


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def plain(value: Any) -> Any:
    """Converts the Decimals boto3 returns into ints and floats, recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def _expires_at() -> int:
    return int(time.time()) + SEVEN_DAYS_SECONDS


def outbox_put(table: str, message: Message) -> TransactItem:
    """Stores a message in the same transaction as the state change it describes."""
    return {
        "Put": {
            "TableName": table,
            "Item": {
                "PK": f"OUTBOX#{message.message_id}",
                "SK": "META",
                "route": "COMMAND" if message.type in COMMAND_QUEUE_ENV else "EVENT",
                "message": to_wire(message),
                "expiresAt": _expires_at(),
            },
        }
    }


def inbox_put(table: str, message_id: str) -> TransactItem:
    """Fails the whole transaction if this message has already been processed."""
    return {
        "Put": {
            "TableName": table,
            "Item": {"PK": f"INBOX#{message_id}", "SK": "META", "expiresAt": _expires_at()},
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


def transact(items: list[TransactItem]) -> None:
    client().transact_write_items(TransactItems=items)


def condition_failed_indexes(err: ClientError) -> list[int] | None:
    """
    Returns the indexes of the transaction items whose condition failed.

    Returns None for anything else, such as a transaction conflict or throttling,
    which callers should re-raise so that SQS redelivers the message.
    """
    if err.response["Error"]["Code"] != "TransactionCanceledException":
        return None
    failed: list[int] = []
    for index, reason in enumerate(err.response.get("CancellationReasons", [])):
        code = reason.get("Code")
        if code == "ConditionalCheckFailed":
            failed.append(index)
        elif code and code != "None":
            return None
    return failed or None


def is_conditional_check_failure(err: ClientError) -> bool:
    return bool(err.response["Error"]["Code"] == "ConditionalCheckFailedException")
