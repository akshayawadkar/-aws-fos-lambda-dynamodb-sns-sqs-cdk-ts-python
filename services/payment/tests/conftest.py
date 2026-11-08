from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from fos_shared import dynamo

TABLE = "payment-test"


@pytest.fixture(autouse=True)
def table(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_NAME", TABLE)
    dynamo.client.cache_clear()
    with mock_aws():
        yield boto3.resource("dynamodb").create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    dynamo.client.cache_clear()


def outbox(table: Any) -> list[dict[str, Any]]:
    """The messages waiting in the outbox."""
    return [dynamo.plain(item["message"]) for item in table.scan()["Items"] if item["PK"].startswith("OUTBOX#")]
