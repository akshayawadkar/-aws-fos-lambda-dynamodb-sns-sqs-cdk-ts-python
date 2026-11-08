import os
from typing import Annotated, Any
from uuid import uuid4

from pydantic import Field

from fos_shared.contracts import (
    SOURCE_ORDER,
    DebitPayment,
    DebitPaymentData,
    Id,
    Model,
    PositiveInt,
    new_message,
)
from fos_shared.dynamo import client, now_iso, outbox_put, plain, table_name, transact
from fos_shared.http import ApiEvent, HttpError, api_handler, parse_body, path_param
from fos_order.keys import ACTIVE_ORDERS_PK, catalog_key, order_key, step_key


class CreateOrderItem(Model):
    product_id: Id
    quantity: PositiveInt


class CreateOrderBody(Model):
    account_id: Id
    restaurant_id: Id
    items: Annotated[list[CreateOrderItem], Field(min_length=1)]


def _max_items() -> int:
    return int(os.environ.get("MAX_ITEMS_PER_ORDER", "10"))


def create_order(event: ApiEvent) -> tuple[int, Any]:
    table = table_name()
    body = parse_body(event, CreateOrderBody)

    if sum(item.quantity for item in body.items) > _max_items():
        raise HttpError(422, f"An order can contain at most {_max_items()} items")

    # Prices come from Order's own catalog copy, never from the client.
    catalog: dict[str, Any] = {}
    for product_id in {item.product_id for item in body.items}:
        result = client().get_item(
            TableName=table, Key=catalog_key(body.restaurant_id, product_id)
        )
        catalog[product_id] = result.get("Item")
    unknown = sorted(product_id for product_id, product in catalog.items() if product is None)
    if unknown:
        raise HttpError(422, "Some products are not on this restaurant", {"productIds": unknown})

    order_items = [
        {
            "productId": item.product_id,
            "name": catalog[item.product_id]["name"],
            "quantity": item.quantity,
            "unitPriceMinor": plain(catalog[item.product_id]["priceMinor"]),
        }
        for item in body.items
    ]
    total_minor = sum(item["quantity"] * item["unitPriceMinor"] for item in order_items)

    order_id = str(uuid4())
    now = now_iso()
    command = new_message(
        DebitPayment,
        source=SOURCE_ORDER,
        correlation_id=order_id,
        causation_id=None,
        data=DebitPaymentData(
            order_id=order_id,
            account_id=body.account_id,
            amount_minor=total_minor,
            currency="USD",
        ),
    )

    # The order and its first command commit together, so neither can exist without the other.
    transact(
        [
            {
                "Put": {
                    "TableName": table,
                    "Item": {
                        **order_key(order_id),
                        "orderId": order_id,
                        "accountId": body.account_id,
                        "restaurantId": body.restaurant_id,
                        "items": order_items,
                        "totalMinor": total_minor,
                        "status": "PAYMENT_PENDING",
                        "version": 1,
                        "createdAt": now,
                        "updatedAt": now,
                        "sweepPk": ACTIVE_ORDERS_PK,
                        "sweepSk": now,
                    },
                }
            },
            {
                "Put": {
                    "TableName": table,
                    "Item": {
                        **step_key(order_id, 1),
                        "status": "PAYMENT_PENDING",
                        "detail": "Order created",
                        "messageId": command.message_id,
                        "at": now,
                    },
                }
            },
            outbox_put(table, command),
        ]
    )

    return 202, {"trackingId": order_id, "status": "PAYMENT_PENDING", "totalMinor": total_minor}


def get_order(event: ApiEvent) -> tuple[int, Any]:
    order_id = path_param(event, "orderId")
    items = client().query(
        TableName=table_name(),
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": f"ORDER#{order_id}"},
        ConsistentRead=True,
    ).get("Items", [])
    meta = next((item for item in items if item["SK"] == "META"), None)
    if meta is None:
        raise HttpError(404, "Order not found")

    return 200, {
        "trackingId": meta["orderId"],
        "status": meta["status"],
        "restaurantId": meta["restaurantId"],
        "items": meta["items"],
        "totalMinor": meta["totalMinor"],
        "createdAt": meta["createdAt"],
        "updatedAt": meta["updatedAt"],
        "steps": [
            {"status": item["status"], "detail": item["detail"], "at": item["at"]}
            for item in items
            if item["SK"].startswith("STEP#")
        ],
    }


handler = api_handler({"POST /orders": create_order, "GET /orders/{orderId}": get_order})
