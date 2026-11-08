"""Publishes outbox rows from a DynamoDB stream to SQS (commands) or EventBridge (events)."""

import json
import os
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer

from contracts import COMMAND_QUEUE_ENV
from dynamo import plain
from log import log

_deserializer = TypeDeserializer()


@lru_cache(maxsize=1)
def _sqs() -> Any:
    return boto3.client("sqs")


@lru_cache(maxsize=1)
def _event_bridge() -> Any:
    return boto3.client("events")


def _publish(route: str, message: dict[str, Any]) -> None:
    if route == "COMMAND":
        queue_env = COMMAND_QUEUE_ENV[message["type"]]
        _sqs().send_message(QueueUrl=os.environ[queue_env], MessageBody=json.dumps(message))
        return

    result = _event_bridge().put_events(
        Entries=[
            {
                "EventBusName": os.environ["EVENT_BUS_NAME"],
                "Source": message["source"],
                "DetailType": message["type"],
                "Detail": json.dumps(message),
            }
        ]
    )
    # PutEvents reports per-entry failures in a successful response rather than raising.
    if result.get("FailedEntryCount"):
        raise RuntimeError(f"PutEvents failed: {result['Entries'][0].get('ErrorMessage')}")


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """
    Delivery is at-least-once, so consumers must be idempotent. On failure it
    reports that record's sequence number and Lambda retries from there, which
    preserves shard order.
    """
    for record in event["Records"]:
        sequence_number = record["dynamodb"]["SequenceNumber"]
        try:
            image = {
                key: _deserializer.deserialize(value)
                for key, value in record["dynamodb"]["NewImage"].items()
            }
            message = plain(image["message"])
            _publish(image["route"], message)
            log(
                "info",
                "Outbox message published",
                messageId=message["messageId"],
                type=message["type"],
                correlationId=message["correlationId"],
            )
        except Exception as err:
            log("error", "Outbox publish failed", sequenceNumber=sequence_number, error=str(err))
            return {"batchItemFailures": [{"itemIdentifier": sequence_number}]}
    return {"batchItemFailures": []}
