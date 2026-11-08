from typing import Any


def account_key(account_id: str) -> dict[str, str]:
    return {"PK": f"ACCOUNT#{account_id}", "SK": "META"}


def debit_key(order_id: str) -> dict[str, str]:
    return {"PK": f"PAYMENT#{order_id}", "SK": "DEBIT"}


def refund_key(order_id: str) -> dict[str, str]:
    return {"PK": f"PAYMENT#{order_id}", "SK": "REFUND"}


def ledger_entry(
    account_id: str,
    entry_id: str,
    entry_type: str,
    amount_minor: int,
    created_at: str,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Ledger entries are append-only, and the ULID sort key keeps them in time order."""
    item: dict[str, Any] = {
        "PK": f"ACCOUNT#{account_id}",
        "SK": f"LEDGER#{entry_id}",
        "entryId": entry_id,
        "type": entry_type,
        "amountMinor": amount_minor,
        "createdAt": created_at,
    }
    if order_id is not None:
        item["orderId"] = order_id
    return item
