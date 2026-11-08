from botocore.exceptions import ClientError

from fos_shared.contracts import (
    Message,
    PaymentCompleted,
    PaymentFailed,
    PaymentRefunded,
    ProductChanged,
    RestaurantConfirmed,
    RestaurantRejected,
)
from fos_shared.dynamo import (
    TransactItem,
    client,
    condition_failed_indexes,
    inbox_put,
    is_conditional_check_failure,
    now_iso,
    outbox_put,
    plain,
    table_name,
    transact,
)
from fos_shared.log import log
from fos_shared.sqs import sqs_handler
from fos_order.keys import catalog_key, order_key, step_key
from fos_order.saga import TERMINAL_STATUSES, Order, transition


def apply_product_changed(event: ProductChanged) -> None:
    """Keeps the local catalog copy current, ignoring anything older than what's stored."""
    data = event.data
    try:
        client().put_item(
            TableName=table_name(),
            Item={
                **catalog_key(data.restaurant_id, data.product_id),
                "productId": data.product_id,
                "name": data.name,
                "priceMinor": data.price_minor,
                "version": data.version,
            },
            ConditionExpression="attribute_not_exists(PK) OR #version < :version",
            ExpressionAttributeNames={"#version": "version"},
            ExpressionAttributeValues={":version": data.version},
        )
    except ClientError as err:
        if not is_conditional_check_failure(err):
            raise
        log("info", "Stale ProductChanged ignored", productId=data.product_id, version=data.version)


def apply_saga_event(event: Message, order_id: str) -> None:
    """Advances the saga one step: new status, step history and next command commit together."""
    table = table_name()
    item = client().get_item(
        TableName=table, Key=order_key(order_id), ConsistentRead=True
    ).get("Item")
    if item is None:
        raise LookupError(f"Order {order_id} not found for {event.type}")
    order = Order.model_validate(plain(item))

    next_step = transition(order, event)
    if next_step is None:
        log(
            "info",
            "Event ignored for current status",
            correlationId=order_id,
            type=event.type,
            status=order.status,
        )
        return

    now = now_iso()
    version = order.version + 1
    update_expression = (
        "SET #status = :status, #version = :next, updatedAt = :now REMOVE sweepPk, sweepSk"
        if next_step.status in TERMINAL_STATUSES
        else "SET #status = :status, #version = :next, updatedAt = :now, sweepSk = :now"
    )

    items: list[TransactItem] = [
        inbox_put(table, event.message_id),
        {
            "Update": {
                "TableName": table,
                "Key": order_key(order_id),
                "UpdateExpression": update_expression,
                # Rejects the write if another event advanced the order after the read above.
                "ConditionExpression": "#version = :current",
                "ExpressionAttributeNames": {"#status": "status", "#version": "version"},
                "ExpressionAttributeValues": {
                    ":status": next_step.status,
                    ":next": version,
                    ":current": order.version,
                    ":now": now,
                },
            }
        },
        {
            "Put": {
                "TableName": table,
                "Item": {
                    **step_key(order_id, version),
                    "status": next_step.status,
                    "detail": next_step.detail,
                    "messageId": event.message_id,
                    "at": now,
                },
            }
        },
    ]
    if next_step.command is not None:
        items.append(outbox_put(table, next_step.command))

    try:
        transact(items)
    except ClientError as err:
        failed = condition_failed_indexes(err)
        if failed is None:
            raise
        if 0 in failed:
            log("info", "Duplicate event ignored", correlationId=order_id, type=event.type)
            return
        raise RuntimeError(f"Order {order_id} changed while applying {event.type}; retrying") from err


def _handle(message: Message) -> None:
    if isinstance(message, ProductChanged):
        apply_product_changed(message)
    elif isinstance(
        message,
        PaymentCompleted | PaymentFailed | PaymentRefunded | RestaurantConfirmed | RestaurantRejected,
    ):
        apply_saga_event(message, message.data.order_id)
    else:
        raise ValueError(f"Order service can't handle {message.type}")


handler = sqs_handler(_handle)
