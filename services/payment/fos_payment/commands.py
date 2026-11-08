"""Handles DebitPayment and RefundPayment from the PaymentCommands queue.

    DebitPayment   -> PaymentCompleted | PaymentFailed
    RefundPayment  -> PaymentRefunded

Every result is written to the outbox in the same transaction as the state change and the
inbox marker, so a redelivered command changes nothing and publishes nothing.
"""
from botocore.exceptions import ClientError
from pydantic import BaseModel

from fos_shared.contracts import (
    SOURCE_PAYMENT,
    DebitPayment,
    Envelope,
    Message,
    PaymentCompleted,
    PaymentCompletedData,
    PaymentFailed,
    PaymentFailedData,
    PaymentFailureReason,
    PaymentRefunded,
    PaymentRefundedData,
    RefundPayment,
    new_message,
)
from fos_shared.dynamo import TransactItem, condition_failed_indexes, inbox_put, outbox_put, table_name, transact
from fos_shared.log import log
from fos_shared.sqs import sqs_handler

from . import store


class Conflict(Exception):
    """A concurrent write changed the state this command read. SQS redelivers it and it runs again."""


def _reply(command: Envelope, event_type: type[Envelope], data: BaseModel, changes: list[TransactItem]) -> None:
    """Commits `changes` with the inbox marker and the resulting event. The inbox marker is item 0."""
    event = new_message(event_type, source=SOURCE_PAYMENT, correlation_id=command.correlation_id,
                        causation_id=command.message_id, data=data)
    items = [inbox_put(table_name(), command.message_id), *changes, outbox_put(table_name(), event)]
    try:
        transact(items)
    except ClientError as err:
        failed = condition_failed_indexes(err)
        if failed and 0 in failed:
            log("info", "Duplicate command ignored", messageId=command.message_id,
                correlationId=command.correlation_id)
            return
        if failed:
            raise Conflict(f"{command.type} conflicted on transaction items {failed}") from err
        raise
    log("info", f"{command.type} -> {event_type.__name__}", orderId=getattr(data, "order_id", None),
        correlationId=command.correlation_id)


def _fail(command: DebitPayment, reason: PaymentFailureReason) -> None:
    _reply(command, PaymentFailed, PaymentFailedData(order_id=command.data.order_id, reason=reason), [])


def debit(command: DebitPayment) -> None:
    data = command.data
    entry_id = store.new_entry_id()
    changes = [
        store.payment_put({"orderId": data.order_id, "accountId": data.account_id, "status": "DEBITED",
                           "amountMinor": data.amount_minor, "debitEntryId": entry_id}),
        store.balance_change(data.account_id, -data.amount_minor, require_active=True),
        store.ledger_put(data.account_id, entry_id, "DEBIT", data.amount_minor, data.order_id),
    ]
    try:
        _reply(command, PaymentCompleted, PaymentCompletedData(order_id=data.order_id, ledger_entry_id=entry_id),
               changes)
        return
    except Conflict:
        pass  # Work out why from the current state.

    payment = store.get_payment(data.order_id)
    if payment is not None:
        if payment["status"] == "REFUNDED":
            _fail(command, "REFUNDED_BEFORE_DEBIT")
        else:
            # The order was already debited by an earlier command: report that debit again.
            _reply(command, PaymentCompleted,
                   PaymentCompletedData(order_id=data.order_id, ledger_entry_id=payment["debitEntryId"]), [])
        return

    account = store.get_account(data.account_id)
    if account is None:
        _fail(command, "ACCOUNT_NOT_FOUND")
    elif account["status"] != "ACTIVE":
        _fail(command, "ACCOUNT_INACTIVE")
    elif account["balanceMinor"] < data.amount_minor:
        _fail(command, "INSUFFICIENT_FUNDS")
    else:
        raise Conflict(f"Debit for order {data.order_id} failed but the account now allows it; retrying")


def refund(command: RefundPayment) -> None:
    data = command.data
    payment = store.get_payment(data.order_id)

    if payment is None:
        # Refund overtook its debit: leave a marker so the debit fails with REFUNDED_BEFORE_DEBIT.
        marker = store.payment_put({"orderId": data.order_id, "accountId": data.account_id, "status": "REFUNDED",
                                    "amountMinor": 0})
        _reply(command, PaymentRefunded, PaymentRefundedData(order_id=data.order_id, ledger_entry_id=None), [marker])
    elif payment["status"] == "REFUNDED":
        _reply(command, PaymentRefunded,
               PaymentRefundedData(order_id=data.order_id, ledger_entry_id=payment.get("refundEntryId")), [])
    else:
        # Give back what was actually debited, whatever the command says.
        amount = payment["amountMinor"]
        if amount != data.amount_minor:
            log("warning", "Refund amount differs from debit; refunding the debit", orderId=data.order_id,
                requested=data.amount_minor, debited=amount, correlationId=command.correlation_id)
        entry_id = store.new_entry_id()
        _reply(command, PaymentRefunded, PaymentRefundedData(order_id=data.order_id, ledger_entry_id=entry_id), [
            store.payment_mark_refunded(data.order_id, entry_id),
            store.balance_change(payment["accountId"], amount),
            store.ledger_put(payment["accountId"], entry_id, "REFUND", amount, data.order_id),
        ])


def handle(message: Message) -> None:
    if isinstance(message, DebitPayment):
        debit(message)
    elif isinstance(message, RefundPayment):
        refund(message)
    else:
        raise ValueError(f"Payment service does not handle {message.type}")


handler = sqs_handler(handle)
