import json
from typing import Any

import pytest
from boto3.dynamodb.types import TypeSerializer

from fos_shared import outbox_publisher
from fos_shared.contracts import (
    SOURCE_ORDER,
    SOURCE_PAYMENT,
    DebitPayment,
    DebitPaymentData,
    PaymentFailed,
    PaymentFailedData,
    new_message,
    parse_message,
)
from fos_shared.dynamo import outbox_put


class FakeEvents:
    def __init__(self, fail: bool = False) -> None:
        self.entries: list[dict[str, Any]] = []
        self.fail = fail

    def put_events(self, Entries: list[dict[str, Any]]) -> dict[str, Any]:
        self.entries.extend(Entries)
        if self.fail:
            return {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "X", "ErrorMessage": "boom"}]}
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "1"}]}


class FakeSqs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_message(self, QueueUrl: str, MessageBody: str) -> dict[str, Any]:
        self.sent.append({"QueueUrl": QueueUrl, "MessageBody": MessageBody})
        return {}


def stream_event(*messages: Any) -> dict[str, Any]:
    serializer = TypeSerializer()
    return {
        "Records": [
            {
                "eventName": "INSERT",
                "dynamodb": {
                    "SequenceNumber": str(100 + i),
                    "NewImage": {k: serializer.serialize(v) for k, v in outbox_put("t", m)["Put"]["Item"].items()},
                },
            }
            for i, m in enumerate(messages)
        ]
    }


def failed_event() -> PaymentFailed:
    return new_message(PaymentFailed, source=SOURCE_PAYMENT, correlation_id="o1", causation_id="c1",
                       data=PaymentFailedData(order_id="o1", reason="INSUFFICIENT_FUNDS"))


def debit_command() -> DebitPayment:
    return new_message(DebitPayment, source=SOURCE_ORDER, correlation_id="o1", causation_id=None,
                       data=DebitPaymentData(order_id="o1", account_id="a1", amount_minor=5, currency="USD"))


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeEvents, FakeSqs]:
    events, sqs = FakeEvents(), FakeSqs()
    monkeypatch.setattr(outbox_publisher, "_event_bridge", lambda: events)
    monkeypatch.setattr(outbox_publisher, "_sqs", lambda: sqs)
    monkeypatch.setenv("EVENT_BUS_NAME", "bus-name")
    monkeypatch.setenv("PAYMENT_COMMANDS_QUEUE_URL", "https://queue/payment")
    return events, sqs


def test_event_goes_to_bus(clients: tuple[FakeEvents, FakeSqs]) -> None:
    events, sqs = clients
    event = failed_event()

    assert outbox_publisher.handler(stream_event(event)) == {"batchItemFailures": []}

    [entry] = events.entries
    assert entry["EventBusName"] == "bus-name" and entry["Source"] == "fos.payment"
    assert entry["DetailType"] == "PaymentFailed"
    assert parse_message(json.loads(entry["Detail"])) == event
    assert sqs.sent == []


def test_command_goes_to_its_queue(clients: tuple[FakeEvents, FakeSqs]) -> None:
    _, sqs = clients
    command = debit_command()

    outbox_publisher.handler(stream_event(command))

    [sent] = sqs.sent
    assert sent["QueueUrl"] == "https://queue/payment"
    assert parse_message(json.loads(sent["MessageBody"])) == command


def test_failure_stops_at_the_failed_record(monkeypatch: pytest.MonkeyPatch,
                                            clients: tuple[FakeEvents, FakeSqs]) -> None:
    _, sqs = clients
    monkeypatch.setattr(outbox_publisher, "_event_bridge", lambda: FakeEvents(fail=True))

    result = outbox_publisher.handler(stream_event(failed_event(), debit_command()))

    # Lambda retries from this sequence number, so the later record isn't published out of order.
    assert result == {"batchItemFailures": [{"itemIdentifier": "100"}]}
    assert sqs.sent == []
