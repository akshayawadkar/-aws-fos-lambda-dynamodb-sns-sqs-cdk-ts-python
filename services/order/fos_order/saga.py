from dataclasses import dataclass
from typing import Literal

from fos_shared.contracts import (
    SOURCE_ORDER,
    ConfirmOrder,
    ConfirmOrderData,
    ConfirmOrderItem,
    Id,
    Message,
    Model,
    PaymentCompleted,
    PaymentFailed,
    PaymentRefunded,
    PositiveInt,
    RefundPayment,
    RefundPaymentData,
    RestaurantConfirmed,
    RestaurantRejected,
    new_message,
)

OrderStatus = Literal[
    "PAYMENT_PENDING",
    "RESTAURANT_PENDING",
    "REFUND_PENDING",
    "CONFIRMED",
    "FAILED",
    "CANCELLED",
]

TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset({"CONFIRMED", "FAILED", "CANCELLED"})


class OrderItem(Model):
    product_id: Id
    name: Id
    quantity: PositiveInt
    unit_price_minor: PositiveInt


class Order(Model):
    order_id: Id
    account_id: Id
    restaurant_id: Id
    items: list[OrderItem]
    total_minor: PositiveInt
    status: OrderStatus
    version: PositiveInt


@dataclass(frozen=True)
class Transition:
    status: OrderStatus
    detail: str
    # The next command to send. None when this step ends the saga.
    command: Message | None = None


def transition(order: Order, event: Message) -> Transition | None:
    """
    The saga's whole decision logic. Returns None when the event doesn't apply to
    the order's current status, which is how late and duplicate events are ignored.
    """
    if isinstance(event, PaymentCompleted):
        if order.status != "PAYMENT_PENDING":
            return None
        return Transition(
            status="RESTAURANT_PENDING",
            detail="Payment completed",
            command=new_message(
                ConfirmOrder,
                source=SOURCE_ORDER,
                correlation_id=order.order_id,
                causation_id=event.message_id,
                data=ConfirmOrderData(
                    order_id=order.order_id,
                    restaurant_id=order.restaurant_id,
                    items=[
                        ConfirmOrderItem(product_id=item.product_id, quantity=item.quantity)
                        for item in order.items
                    ],
                ),
            ),
        )

    if isinstance(event, PaymentFailed):
        if order.status != "PAYMENT_PENDING":
            return None
        return Transition(status="FAILED", detail=f"Payment failed: {event.data.reason}")

    if isinstance(event, RestaurantConfirmed):
        if order.status != "RESTAURANT_PENDING":
            return None
        return Transition(status="CONFIRMED", detail="Restaurant confirmed the order")

    if isinstance(event, RestaurantRejected):
        if order.status != "RESTAURANT_PENDING":
            return None
        return Transition(
            status="REFUND_PENDING",
            detail=f"Restaurant rejected the order: {event.data.reason}",
            command=new_message(
                RefundPayment,
                source=SOURCE_ORDER,
                correlation_id=order.order_id,
                causation_id=event.message_id,
                data=RefundPaymentData(
                    order_id=order.order_id,
                    account_id=order.account_id,
                    amount_minor=order.total_minor,
                    reason=event.data.reason,
                ),
            ),
        )

    if isinstance(event, PaymentRefunded):
        if order.status != "REFUND_PENDING":
            return None
        return Transition(status="CANCELLED", detail="Payment refunded and order cancelled")

    return None
