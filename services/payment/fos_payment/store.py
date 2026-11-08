"""Payment table layout and the transaction items that change it. All amounts are integer minor units (cents).

    PK=ACCOUNT#<accountId>  SK=META           accountId, ownerName, status, currency, balanceMinor, createdAt
    PK=ACCOUNT#<accountId>  SK=LEDGER#<ulid>  entryId, type, amountMinor, orderId?, createdAt
    PK=ORDER#<orderId>      SK=PAYMENT        orderId, accountId, status, amountMinor, debitEntryId?, refundEntryId?

The ORDER# row makes debits and refunds idempotent per order, and a refund that arrives before its
debit leaves a REFUNDED row behind so the late debit is rejected.
"""
from typing import Any, Literal

from boto3.dynamodb.conditions import Key
from ulid import ULID

from fos_shared.dynamo import TransactItem, client, now_iso, plain, table_name

LedgerType = Literal["OPENING", "CREDIT", "DEBIT", "REFUND"]


def _account_key(account_id: str) -> dict[str, str]:
    return {"PK": f"ACCOUNT#{account_id}", "SK": "META"}


def _payment_key(order_id: str) -> dict[str, str]:
    return {"PK": f"ORDER#{order_id}", "SK": "PAYMENT"}


def _view(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {key: value for key, value in plain(item).items() if key not in ("PK", "SK")}


def new_entry_id() -> str:
    """ULIDs sort by creation time, so ledger SKs come back in order."""
    return str(ULID())


def get_account(account_id: str) -> dict[str, Any] | None:
    return _view(client().get_item(TableName=table_name(), Key=_account_key(account_id)).get("Item"))


def get_payment(order_id: str) -> dict[str, Any] | None:
    return _view(client().get_item(TableName=table_name(), Key=_payment_key(order_id)).get("Item"))


def list_ledger(account_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first."""
    response = client().query(
        TableName=table_name(),
        KeyConditionExpression=Key("PK").eq(f"ACCOUNT#{account_id}") & Key("SK").begins_with("LEDGER#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return [_view(item) for item in response["Items"]]  # type: ignore[misc]


def account_put(account: dict[str, Any]) -> TransactItem:
    return {
        "Put": {
            "TableName": table_name(),
            "Item": {**_account_key(account["accountId"]), **account},
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


def ledger_put(
        account_id: str, entry_id: str, entry_type: LedgerType, amount_minor: int, order_id: str | None = None
) -> TransactItem:
    item: dict[str, Any] = {
        "PK": f"ACCOUNT#{account_id}",
        "SK": f"LEDGER#{entry_id}",
        "entryId": entry_id,
        "type": entry_type,
        "amountMinor": amount_minor,
        "createdAt": now_iso(),
    }
    if order_id is not None:
        item["orderId"] = order_id
    return {"Put": {"TableName": table_name(), "Item": item}}


def balance_change(account_id: str, delta_minor: int, *, require_active: bool = False) -> TransactItem:
    """Adds `delta_minor` to the balance. The account must exist, and a debit can't overdraw it."""
    conditions = ["attribute_exists(PK)"]
    values: dict[str, Any] = {":delta": delta_minor}
    names: dict[str, str] = {}
    if require_active:
        conditions.append("#status = :active")
        values[":active"] = "ACTIVE"
        names["#status"] = "status"
    if delta_minor < 0:
        conditions.append("balanceMinor >= :needed")
        values[":needed"] = -delta_minor
    update: dict[str, Any] = {
        "TableName": table_name(),
        "Key": _account_key(account_id),
        "UpdateExpression": "ADD balanceMinor :delta",
        "ConditionExpression": " AND ".join(conditions),
        "ExpressionAttributeValues": values,
    }
    if names:
        update["ExpressionAttributeNames"] = names
    return {"Update": update}


def payment_put(payment: dict[str, Any]) -> TransactItem:
    """Records the first debit or refund seen for an order. Fails if the order already has one."""
    return {
        "Put": {
            "TableName": table_name(),
            "Item": {**_payment_key(payment["orderId"]), **payment},
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


def payment_mark_refunded(order_id: str, refund_entry_id: str) -> TransactItem:
    return {
        "Update": {
            "TableName": table_name(),
            "Key": _payment_key(order_id),
            "UpdateExpression": "SET #status = :refunded, refundEntryId = :entry",
            "ConditionExpression": "#status = :debited",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {":refunded": "REFUNDED", ":debited": "DEBITED", ":entry": refund_entry_id},
        }
    }
