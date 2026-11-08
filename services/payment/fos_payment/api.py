"""HTTP API for accounts. Bodies and responses are camelCase; amounts are integer minor units."""
from typing import Annotated, Any

from botocore.exceptions import ClientError
from pydantic import Field

from fos_shared.contracts import Id, Model, PositiveInt
from fos_shared.dynamo import condition_failed_indexes, now_iso, transact
from fos_shared.http import ApiEvent, HttpError, api_handler, parse_body, path_param

from . import store


class CreateAccountBody(Model):
    owner_name: Id
    opening_balance_minor: Annotated[int, Field(ge=0, strict=True)] = 0


class CreditBody(Model):
    amount_minor: PositiveInt


def _existing(account_id: str) -> dict[str, Any]:
    account = store.get_account(account_id)
    if account is None:
        raise HttpError(404, f"Account {account_id} not found")
    return account


def create_account(event: ApiEvent) -> tuple[int, Any]:
    body = parse_body(event, CreateAccountBody)
    account = {
        "accountId": store.new_entry_id(),
        "ownerName": body.owner_name,
        "status": "ACTIVE",
        "currency": "USD",
        "balanceMinor": body.opening_balance_minor,
        "createdAt": now_iso(),
    }
    items = [store.account_put(account)]
    if body.opening_balance_minor:
        items.append(store.ledger_put(account["accountId"], store.new_entry_id(), "OPENING",
                                      body.opening_balance_minor))
    transact(items)
    return 201, account


def get_account(event: ApiEvent) -> tuple[int, Any]:
    return 200, _existing(path_param(event, "accountId"))


def credit(event: ApiEvent) -> tuple[int, Any]:
    account_id = path_param(event, "accountId")
    body = parse_body(event, CreditBody)
    try:
        transact([
            store.balance_change(account_id, body.amount_minor),
            store.ledger_put(account_id, store.new_entry_id(), "CREDIT", body.amount_minor),
        ])
    except ClientError as err:
        if condition_failed_indexes(err) == [0]:
            raise HttpError(404, f"Account {account_id} not found") from err
        raise
    return 200, _existing(account_id)


def ledger(event: ApiEvent) -> tuple[int, Any]:
    account_id = path_param(event, "accountId")
    _existing(account_id)
    return 200, {"accountId": account_id, "entries": store.list_ledger(account_id)}


handler = api_handler(
    {
        "POST /accounts": create_account,
        "GET /accounts/{accountId}": get_account,
        "POST /accounts/{accountId}/credit": credit,
        "GET /accounts/{accountId}/ledger": ledger,
    }
)
