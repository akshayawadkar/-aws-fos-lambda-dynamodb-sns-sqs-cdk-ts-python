from botocore.exceptions import ClientError
from ulid import ULID

from fos_shared.contracts import (
    SOURCE_PAYMENT,
    DebitPayment,
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
from fos_shared.dynamo import (
    TransactItem,
    client,
    condition_failed_indexes,
    inbox_put,
    now_iso,
    outbox_put,
    plain,
    table_name,
    transact,
)
from fos_shared.log import log
from fos_shared.sqs import sqs_handler
from .keys import account_key, debit_key, ledger_entry, refund_key


def _debit_failure_reason(table: str, account_id: str, amount_minor: int) -> PaymentFailureReason:
    """Works out why the balance update's condition failed."""
    account = client().get_item(
        TableName=table, Key=account_key(account_id), ConsistentRead=True
    ).get("Item")
    if account is None:
        return "ACCOUNT_NOT_FOUND"
    if account["status"] != "ACTIVE":
        return "ACCOUNT_INACTIVE"
    if account["balanceMinor"] < amount_minor:
        return "INSUFFICIENT_FUNDS"
    # The balance changed since the transaction ran, so let SQS retry it.
    raise RuntimeError(f"Debit condition failed but account {account_id} can now cover it")


def _record_debit_failure(table: str, command: DebitPayment, reason: PaymentFailureReason) -> None:
    data = command.data
    event = new_message(
        PaymentFailed,
        source=SOURCE_PAYMENT,
        correlation_id=data.order_id,
        causation_id=command.message_id,
        data=PaymentFailedData(order_id=data.order_id, reason=reason),
    )
    try:
        transact(
            [
                inbox_put(table, command.message_id),
                {
                    "Put": {
                        "TableName": table,
                        "Item": {
                            **debit_key(data.order_id),
                            "status": "FAILED",
                            "reason": reason,
                            "accountId": data.account_id,
                            "amountMinor": data.amount_minor,
                            "createdAt": now_iso(),
                        },
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                outbox_put(table, event),
            ]
        )
    except ClientError as err:
        if condition_failed_indexes(err) is None:
            raise
        log("info", "Duplicate debit failure ignored", correlationId=data.order_id)


def handle_debit(command: DebitPayment) -> None:
    table = table_name()
    data = command.data
    entry_id = str(ULID())
    now = now_iso()
    event = new_message(
        PaymentCompleted,
        source=SOURCE_PAYMENT,
        correlation_id=data.order_id,
        causation_id=command.message_id,
        data=PaymentCompletedData(order_id=data.order_id, ledger_entry_id=entry_id),
    )

    items: list[TransactItem] = [
        inbox_put(table, command.message_id),
        {
            "Put": {
                "TableName": table,
                "Item": {
                    **debit_key(data.order_id),
                    "status": "COMPLETED",
                    "accountId": data.account_id,
                    "amountMinor": data.amount_minor,
                    "ledgerEntryId": entry_id,
                    "createdAt": now,
                },
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        },
        # A refund marker means this order was already given up on, so the debit must not go through.
        {
            "ConditionCheck": {
                "TableName": table,
                "Key": refund_key(data.order_id),
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        },
        {
            "Update": {
                "TableName": table,
                "Key": account_key(data.account_id),
                "UpdateExpression": "SET balanceMinor = balanceMinor - :amount, updatedAt = :now",
                "ConditionExpression": (
                    "attribute_exists(PK) AND #status = :active AND balanceMinor >= :amount"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":amount": data.amount_minor,
                    ":active": "ACTIVE",
                    ":now": now,
                },
            }
        },
        {
            "Put": {
                "TableName": table,
                "Item": ledger_entry(
                    data.account_id, entry_id, "DEBIT", data.amount_minor, now, data.order_id
                ),
            }
        },
        outbox_put(table, event),
    ]

    try:
        transact(items)
    except ClientError as err:
        failed = condition_failed_indexes(err)
        if failed is None:
            raise
        if 0 in failed or 1 in failed:
            log("info", "Duplicate debit ignored", correlationId=data.order_id)
            return
        reason: PaymentFailureReason = (
            "REFUNDED_BEFORE_DEBIT"
            if 2 in failed
            else _debit_failure_reason(table, data.account_id, data.amount_minor)
        )
        _record_debit_failure(table, command, reason)


def handle_refund(command: RefundPayment) -> None:
    table = table_name()
    order_id = command.data.order_id
    now = now_iso()

    debit = client().get_item(
        TableName=table, Key=debit_key(order_id), ConsistentRead=True
    ).get("Item")
    completed = debit is not None and debit["status"] == "COMPLETED"
    entry_id = str(ULID()) if completed else None
    event = new_message(
        PaymentRefunded,
        source=SOURCE_PAYMENT,
        correlation_id=order_id,
        causation_id=command.message_id,
        data=PaymentRefundedData(order_id=order_id, ledger_entry_id=entry_id),
    )

    items: list[TransactItem] = [
        inbox_put(table, command.message_id),
        {
            "Put": {
                "TableName": table,
                "Item": {
                    **refund_key(order_id),
                    "status": "REFUNDED" if completed else "NOTHING_TO_REFUND",
                    "reason": command.data.reason,
                    "ledgerEntryId": entry_id,
                    "createdAt": now,
                },
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        },
    ]

    if debit is not None and entry_id is not None:
        # Refund what was actually debited, not what the command claims.
        account_id = str(debit["accountId"])
        amount_minor = int(plain(debit["amountMinor"]))
        items += [
            {
                "Update": {
                    "TableName": table,
                    "Key": account_key(account_id),
                    "UpdateExpression": "SET balanceMinor = balanceMinor + :amount, updatedAt = :now",
                    "ExpressionAttributeValues": {":amount": amount_minor, ":now": now},
                }
            },
            {
                "Put": {
                    "TableName": table,
                    "Item": ledger_entry(account_id, entry_id, "CREDIT", amount_minor, now, order_id),
                }
            },
        ]
    elif debit is not None:
        # Guards against the failed debit record changing after the read above.
        items.append(
            {
                "ConditionCheck": {
                    "TableName": table,
                    "Key": debit_key(order_id),
                    "ConditionExpression": "#status = :failed",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {":failed": "FAILED"},
                }
            }
        )
    else:
        # Guards against a debit landing between the read above and this write.
        items.append(
            {
                "ConditionCheck": {
                    "TableName": table,
                    "Key": debit_key(order_id),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            }
        )
    items.append(outbox_put(table, event))

    try:
        transact(items)
    except ClientError as err:
        failed = condition_failed_indexes(err)
        if failed is None:
            raise
        if 0 in failed or 1 in failed:
            log("info", "Duplicate refund ignored", correlationId=order_id)
            return
        raise RuntimeError(f"Debit for order {order_id} changed during refund; retrying") from err


def _handle(message: Message) -> None:
    if isinstance(message, DebitPayment):
        handle_debit(message)
    elif isinstance(message, RefundPayment):
        handle_refund(message)
    else:
        raise ValueError(f"Payment service can't handle {message.type}")


handler = sqs_handler(_handle)
