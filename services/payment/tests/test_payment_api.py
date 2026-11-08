import json
from typing import Any

from fos_payment import api


def call(method: str, route: str, path_params: dict[str, str] | None = None, body: Any = None) -> tuple[int, Any]:
    event = {
        "routeKey": f"{method} {route}",
        "pathParameters": path_params,
        "body": None if body is None else json.dumps(body),
        "isBase64Encoded": False,
    }
    response = api.handler(event, None)
    return response["statusCode"], json.loads(response["body"])


def create(**body: Any) -> dict[str, Any]:
    status, account = call("POST", "/accounts", body=body)
    assert status == 201, account
    return account


def test_create_and_get_account() -> None:
    account = create(ownerName="Ada", openingBalanceMinor=5000)
    assert account["ownerName"] == "Ada" and account["balanceMinor"] == 5000 and account["status"] == "ACTIVE"

    status, fetched = call("GET", "/accounts/{accountId}", {"accountId": account["accountId"]})
    assert status == 200 and fetched == account


def test_credit_updates_balance_and_ledger() -> None:
    account_id = create(ownerName="Ada", openingBalanceMinor=1000)["accountId"]

    status, credited = call("POST", "/accounts/{accountId}/credit", {"accountId": account_id}, {"amountMinor": 250})
    assert status == 200 and credited["balanceMinor"] == 1250

    status, ledger = call("GET", "/accounts/{accountId}/ledger", {"accountId": account_id})
    assert status == 200
    assert [(e["type"], e["amountMinor"]) for e in ledger["entries"]] == [("CREDIT", 250), ("OPENING", 1000)]


def test_unknown_account_is_404() -> None:
    missing = {"accountId": "nope"}
    assert call("GET", "/accounts/{accountId}", missing)[0] == 404
    assert call("POST", "/accounts/{accountId}/credit", missing, {"amountMinor": 1})[0] == 404
    assert call("GET", "/accounts/{accountId}/ledger", missing)[0] == 404


def test_validation() -> None:
    assert call("POST", "/accounts", body={})[0] == 400
    assert call("POST", "/accounts", body={"ownerName": "Ada", "openingBalanceMinor": -1})[0] == 400
    account_id = create(ownerName="Ada")["accountId"]
    for amount in (0, -5, 1.5, "10"):
        status, _ = call("POST", "/accounts/{accountId}/credit", {"accountId": account_id}, {"amountMinor": amount})
        assert status == 400, amount
