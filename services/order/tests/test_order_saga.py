from typing import Any

import pytest

from fos_order.saga import Order, OrderItem, OrderStatus, transition
from fos_shared.contracts import (
    ConfirmOrder,
    Message,
    PaymentCompleted,
    PaymentCompletedData,
    PaymentFailed,
    PaymentFailedData,
    PaymentRefunded,
    PaymentRefundedData,
    RefundPayment,
    RestaurantConfirmed,
    RestaurantConfirmedData,
    RestaurantRejected,
    RestaurantRejectedData,
    new_message,
)


def order(status: OrderStatus) -> Order:
    return Order(
        order_id="o1",
        account_id="a1",
        restaurant_id="r1",
        items=[OrderItem(product_id="p1", name="Pizza", quantity=2, unit_price_minor=500)],
        total_minor=1000,
        status=status,
        version=1,
    )


def event(message_type: Any, data: Any) -> Any:
    return new_message(
        message_type, source="test", correlation_id="o1", causation_id=None, data=data
    )


PAID = event(PaymentCompleted, PaymentCompletedData(order_id="o1", ledger_entry_id="l1"))
PAYMENT_FAILED = event(PaymentFailed, PaymentFailedData(order_id="o1", reason="INSUFFICIENT_FUNDS"))
CONFIRMED = event(RestaurantConfirmed, RestaurantConfirmedData(order_id="o1"))
REJECTED = event(RestaurantRejected, RestaurantRejectedData(order_id="o1", reason="PRODUCT_UNAVAILABLE"))
REFUNDED = event(PaymentRefunded, PaymentRefundedData(order_id="o1", ledger_entry_id="l2"))


def test_sends_confirm_order_after_payment_completes() -> None:
    next_step = transition(order("PAYMENT_PENDING"), PAID)
    assert next_step is not None
    assert next_step.status == "RESTAURANT_PENDING"
    assert isinstance(next_step.command, ConfirmOrder)
    assert next_step.command.causation_id == PAID.message_id
    assert next_step.command.data.restaurant_id == "r1"
    assert [(i.product_id, i.quantity) for i in next_step.command.data.items] == [("p1", 2)]


def test_fails_without_rollback_when_payment_fails() -> None:
    next_step = transition(order("PAYMENT_PENDING"), PAYMENT_FAILED)
    assert next_step is not None
    assert next_step.status == "FAILED"
    assert "INSUFFICIENT_FUNDS" in next_step.detail
    assert next_step.command is None


def test_confirms_when_the_restaurant_confirms() -> None:
    next_step = transition(order("RESTAURANT_PENDING"), CONFIRMED)
    assert next_step is not None
    assert next_step.status == "CONFIRMED"
    assert next_step.command is None


def test_starts_a_refund_when_the_restaurant_rejects() -> None:
    next_step = transition(order("RESTAURANT_PENDING"), REJECTED)
    assert next_step is not None
    assert next_step.status == "REFUND_PENDING"
    assert isinstance(next_step.command, RefundPayment)
    assert next_step.command.data.amount_minor == 1000
    assert next_step.command.data.reason == "PRODUCT_UNAVAILABLE"


def test_cancels_once_the_refund_completes() -> None:
    next_step = transition(order("REFUND_PENDING"), REFUNDED)
    assert next_step is not None
    assert next_step.status == "CANCELLED"


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("RESTAURANT_PENDING", PAID),
        ("CONFIRMED", PAID),
        ("CONFIRMED", REJECTED),
        ("FAILED", PAID),
        ("PAYMENT_PENDING", CONFIRMED),
        ("CANCELLED", CONFIRMED),
        ("CONFIRMED", REFUNDED),
        ("REFUND_PENDING", REJECTED),
    ],
)
def test_ignores_events_that_do_not_apply(status: OrderStatus, message: Message) -> None:
    assert transition(order(status), message) is None
