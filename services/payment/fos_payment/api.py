from typing import Annotated, Any
from uuid import uuid4

from botocore.exceptions import ClientError
from pydantic import Field
from ulid import ULID

from fos_shared.contracts import Model, PositiveInt
from fos_shared.dynamo import client, condition_failed_indexes, now_iso, table_name, transact
from fos_shared.http import ApiEvent, HttpError, api_handler, parse_body, path_param
from .keys import account_key, ledger_entry


class CreateAccountBody(Model):
    name: Annotated[str, Field(min_length=1)]


class CreditBody(Model):
    amount_minor: PositiveInt
    # Lets a client safely retry a top-up without crediting the account twice.
    reference_id: Annotated[str, Field(min_length=1)]


def _without_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in ("PK", "SK")}


def create_account(event: ApiEvent) -> tuple[int, Any]:
    body = parse_body(event, CreateAccountBody)
    account_id = str(uuid4())
    now = now_iso()
    item = {
        **account_key(account_id),
        "accountId": account_id,
        "name": body.name,
        "balanceMinor": 0,
        "currency": "USD",
        "status": "ACTIVE",
        "createdAt": now,
        "updatedAt": now,
    }
    client().put_item(TableName=table_name(), Item=item)
    return 201, _without_keys(item)


def get_account(event: ApiEvent) -> tuple[int, Any]:
    account_id = path_param(event, "accountId")
    item = client().get_item(
        TableName=table_name(), Key=account_key(account_id), ConsistentRead=True
    ).get("Item")
    if item is None:
        raise HttpError(404, "Account not found")
    return 200, _without_keys(item)


def credit_account(event: ApiEvent) -> tuple[int, Any]:
    table = table_name()
    account_id = path_param(event, "accountId")
    body = parse_body(event, CreditBody)
    entry_id = str(ULID())
    now = now_iso()
    try:
        transact(
            [
                {
                    "Put": {
                        "TableName": table,
                        "Item": {
                            "PK": f"ACCOUNT#{account_id}",
                            "SK": f"CREDITREF#{body.reference_id}",
                            "entryId": entry_id,
                            "createdAt": now,
                        },
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Update": {
                        "TableName": table,
                        "Key": account_key(account_id),
                        "UpdateExpression": "SET balanceMinor = balanceMinor + :amount, updatedAt = :now",
                        "ConditionExpression": "attribute_exists(PK)",
                        "ExpressionAttributeValues": {":amount": body.amount_minor, ":now": now},
                    }
                },
                {
                    "Put": {
                        "TableName": table,
                        "Item": ledger_entry(account_id, entry_id, "CREDIT", body.amount_minor, now),
                    }
                },
            ]
        )
    except ClientError as err:
        failed = condition_failed_indexes(err)
        if failed is None:
            raise
        if 1 in failed:
            raise HttpError(404, "Account not found") from err
        return 200, {"accountId": account_id, "referenceId": body.reference_id, "duplicate": True}
    return 201, {
        "accountId": account_id,
        "referenceId": body.reference_id,
        "entryId": entry_id,
        "amountMinor": body.amount_minor,
        "duplicate": False,
    }


def get_ledger(event: ApiEvent) -> tuple[int, Any]:
    account_id = path_param(event, "accountId")
    items = client().query(
        TableName=table_name(),
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": f"ACCOUNT#{account_id}", ":prefix": "LEDGER#"},
        ScanIndexForward=False,
        Limit=50,
    ).get("Items", [])
    return 200, {"accountId": account_id, "entries": [_without_keys(item) for item in items]}


handler = api_handler(
    {
        "POST /accounts": create_account,
        "GET /accounts/{accountId}": get_account,
        "POST /accounts/{accountId}/credit": credit_account,
        "GET /accounts/{accountId}/ledger": get_ledger,
    }
)
