import json
import uuid
from typing import Any

from conftest import outbox
from fos_payment import commands, store
from fos_payment.api import create_account
from fos_shared.contracts import (
    SOURCE_ORDER,
    DebitPayment,
    DebitPaymentData,
    RefundPayment,
    RefundPaymentData,
    new_message,
    to_wire,
)


def open_account(balance: int) -> str:
    _, account = create_account({"body": json.dumps({"ownerName": "Ada", "openingBalanceMinor": balance})})
    return str(account["accountId"])


def debit(account_id: str, amount: int, order_id: str = "o1") -> DebitPayment:
    return new_message(DebitPayment, source=SOURCE_ORDER, correlation_id=order_id, causation_id=None,
                       data=DebitPaymentData(order_id=order_id, account_id=account_id, amount_minor=amount,
                                             currency="USD"))


def refund(account_id: str, amount: int, order_id: str = "o1") -> RefundPayment:
    return new_message(RefundPayment, source=SOURCE_ORDER, correlation_id=order_id, causation_id=None,
                       data=RefundPaymentData(order_id=order_id, account_id=account_id, amount_minor=amount,
                                              reason="CANCELLED"))


def deliver(*messages: Any) -> dict[str, Any]:
    """Sends wire-format bodies through the SQS handler, as the PaymentCommands queue would."""
    records = [
        {"messageId": str(uuid.uuid4()), "body": json.dumps(m if isinstance(m, dict) else to_wire(m))}
        for m in messages
    ]
    return commands.handler({"Records": records}, None)


def balance(account_id: str) -> int:
    return store.get_account(account_id)["balanceMinor"]  # type: ignore[index]


def test_debit_completes(table: Any) -> None:
    account_id = open_account(1000)
    command = debit(account_id, 300)

    assert deliver(command) == {"batchItemFailures": []}

    assert balance(account_id) == 700
    [event] = outbox(table)
    assert event["type"] == "PaymentCompleted" and event["source"] == "fos.payment"
    assert event["correlationId"] == "o1" and event["causationId"] == command.message_id
    ledger = store.list_ledger(account_id)
    assert event["data"]["ledgerEntryId"] == ledger[0]["entryId"] and ledger[0]["type"] == "DEBIT"


def test_redelivered_debit_is_applied_once(table: Any) -> None:
    account_id = open_account(1000)
    command = debit(account_id, 300)

    deliver(command)
    deliver(command)

    assert balance(account_id) == 700
    assert len(outbox(table)) == 1


def test_second_debit_for_same_order_reports_the_first(table: Any) -> None:
    account_id = open_account(1000)
    deliver(debit(account_id, 300))
    deliver(debit(account_id, 300))

    assert balance(account_id) == 700
    first, second = sorted(outbox(table), key=lambda e: e["occurredAt"])
    assert second["type"] == "PaymentCompleted"
    assert second["data"]["ledgerEntryId"] == first["data"]["ledgerEntryId"]


def test_debit_failures(table: Any) -> None:
    poor = open_account(100)
    deliver(debit(poor, 300, "o1"), debit("missing", 300, "o2"))

    assert balance(poor) == 100
    reasons = {e["data"]["orderId"]: e["data"]["reason"] for e in outbox(table)}
    assert reasons == {"o1": "INSUFFICIENT_FUNDS", "o2": "ACCOUNT_NOT_FOUND"}


def test_refund_returns_the_debited_amount(table: Any) -> None:
    account_id = open_account(1000)
    deliver(debit(account_id, 300))
    deliver(refund(account_id, 999))  # The debit is what gets refunded, not the requested amount.

    assert balance(account_id) == 1000
    refunded = [e for e in outbox(table) if e["type"] == "PaymentRefunded"]
    assert len(refunded) == 1 and refunded[0]["data"]["ledgerEntryId"] is not None


def test_refund_before_debit_blocks_the_debit(table: Any) -> None:
    account_id = open_account(1000)
    deliver(refund(account_id, 300))
    deliver(debit(account_id, 300))

    assert balance(account_id) == 1000
    events = {e["type"]: e["data"] for e in outbox(table)}
    assert events["PaymentRefunded"]["ledgerEntryId"] is None
    assert events["PaymentFailed"]["reason"] == "REFUNDED_BEFORE_DEBIT"


def test_malformed_message_fails_only_itself(table: Any) -> None:
    account_id = open_account(1000)
    result = deliver({"type": "DebitPayment", "data": {}}, debit(account_id, 1))

    assert len(result["batchItemFailures"]) == 1
    assert balance(account_id) == 999
