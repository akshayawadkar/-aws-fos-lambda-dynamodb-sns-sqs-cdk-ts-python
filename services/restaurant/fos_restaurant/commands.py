from botocore.exceptions import ClientError

from fos_shared.contracts import (
    SOURCE_RESTAURANT,
    ConfirmOrder,
    Message,
    RestaurantConfirmed,
    RestaurantConfirmedData,
    RestaurantRejected,
    RestaurantRejectedData,
    new_message,
)
from fos_shared.dynamo import (
    client,
    condition_failed_indexes,
    inbox_put,
    now_iso,
    outbox_put,
    table_name,
    transact,
)
from fos_shared.log import log
from fos_shared.sqs import sqs_handler
from fos_restaurant.decide import decide


def handle_confirm_order(command: ConfirmOrder) -> None:
    table = table_name()
    data = command.data
    pk = f"RESTAURANT#{data.restaurant_id}"

    restaurant = client().get_item(
        TableName=table, Key={"PK": pk, "SK": "META"}, ConsistentRead=True
    ).get("Item")
    products = client().query(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": pk, ":prefix": "PRODUCT#"},
        ConsistentRead=True,
    ).get("Items", [])

    reason = decide(restaurant, products, data.items)
    event: RestaurantConfirmed | RestaurantRejected
    if reason is None:
        event = new_message(
            RestaurantConfirmed,
            source=SOURCE_RESTAURANT,
            correlation_id=data.order_id,
            causation_id=command.message_id,
            data=RestaurantConfirmedData(order_id=data.order_id),
        )
    else:
        event = new_message(
            RestaurantRejected,
            source=SOURCE_RESTAURANT,
            correlation_id=data.order_id,
            causation_id=command.message_id,
            data=RestaurantRejectedData(order_id=data.order_id, reason=reason),
        )

    try:
        transact(
            [
                inbox_put(table, command.message_id),
                {
                    # Keeps the first decision, so a retry can't change the answer after availability changes.
                    "Put": {
                        "TableName": table,
                        "Item": {
                            "PK": f"DECISION#{data.order_id}",
                            "SK": "META",
                            "outcome": "CONFIRMED" if reason is None else "REJECTED",
                            "reason": reason,
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
        log("info", "Duplicate ConfirmOrder ignored", correlationId=data.order_id)


def _handle(message: Message) -> None:
    if not isinstance(message, ConfirmOrder):
        raise ValueError(f"Restaurant service can't handle {message.type}")
    handle_confirm_order(message)


handler = sqs_handler(_handle)
