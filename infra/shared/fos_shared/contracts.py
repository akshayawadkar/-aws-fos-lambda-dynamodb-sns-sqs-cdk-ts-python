"""The single definition of every message: types and runtime validation both derive from it."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeVar
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel



SOURCE_ORDER = "fos.order"
SOURCE_PAYMENT = "fos.payment"
SOURCE_RESTAURANT = "fos.restaurant"


Id = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]

PaymentFailureReason = Literal[
    "INSUFFICIENT_FUNDS",
    "ACCOUNT_NOT_FOUND",
    "ACCOUNT_INACTIVE",
    "REFUNDED_BEFORE_DEBIT",
]
RestaurantRejectionReason = Literal[
    "RESTAURANT_NOT_FOUND",
    "RESTAURANT_CLOSED",
    "PRODUCT_NOT_IN_RESTAURANT",
    "PRODUCT_UNAVAILABLE",
]




class Model(BaseModel):
    """Snake case in Python, camelCase on the wire and in DynamoDB."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class DebitPaymentData(Model):
    order_id: Id
    account_id: Id
    amount_minor: PositiveInt
    currency: Literal["USD"]


class RefundPaymentData(Model):
    order_id: Id
    account_id: Id
    amount_minor: PositiveInt
    reason: Id


class ConfirmOrderItem(Model):
    product_id: Id
    quantity: PositiveInt


class ConfirmOrderData(Model):
    order_id: Id
    restaurant_id: Id
    items: Annotated[list[ConfirmOrderItem], Field(min_length=1)]


class PaymentCompletedData(Model):
    order_id: Id
    ledger_entry_id: Id


class PaymentFailedData(Model):
    order_id: Id
    reason: PaymentFailureReason


class PaymentRefundedData(Model):
    order_id: Id
    # None when there was no completed debit to give back.
    ledger_entry_id: Id | None


class RestaurantConfirmedData(Model):
    order_id: Id


class RestaurantRejectedData(Model):
    order_id: Id
    reason: RestaurantRejectionReason


class ProductChangedData(Model):
    restaurant_id: Id
    product_id: Id
    name: Id
    price_minor: PositiveInt
    version: PositiveInt


class Envelope(Model):
    message_id: Id
    schema_version: Literal[1]
    occurred_at: AwareDatetime
    correlation_id: Id
    causation_id: Id | None
    source: Id


# Commands: sent directly to the owning service's SQS queue.
class DebitPayment(Envelope):
    type: Literal["DebitPayment"]
    data: DebitPaymentData


class RefundPayment(Envelope):
    type: Literal["RefundPayment"]
    data: RefundPaymentData


class ConfirmOrder(Envelope):
    type: Literal["ConfirmOrder"]
    data: ConfirmOrderData


# Events: published to EventBridge, then routed to the consumer's SQS queue.
class PaymentCompleted(Envelope):
    type: Literal["PaymentCompleted"]
    data: PaymentCompletedData


class PaymentFailed(Envelope):
    type: Literal["PaymentFailed"]
    data: PaymentFailedData


class PaymentRefunded(Envelope):
    type: Literal["PaymentRefunded"]
    data: PaymentRefundedData


class RestaurantConfirmed(Envelope):
    type: Literal["RestaurantConfirmed"]
    data: RestaurantConfirmedData


class RestaurantRejected(Envelope):
    type: Literal["RestaurantRejected"]
    data: RestaurantRejectedData


class ProductChanged(Envelope):
    type: Literal["ProductChanged"]
    data: ProductChangedData


Message = (
        DebitPayment
        | RefundPayment
        | ConfirmOrder
        | PaymentCompleted
        | PaymentFailed
        | PaymentRefunded
        | RestaurantConfirmed
        | RestaurantRejected
        | ProductChanged
)

COMMAND_QUEUE_ENV = {
    "DebitPayment": "PAYMENT_COMMANDS_QUEUE_URL",
    "RefundPayment": "PAYMENT_COMMANDS_QUEUE_URL",
    "ConfirmOrder": "RESTAURANT_COMMANDS_QUEUE_URL",
}


_message_adapter: TypeAdapter[Message] = TypeAdapter(
    Annotated[Message, Field(discriminator="type")]
)

M = TypeVar("M", bound=Envelope)


def parse_message(raw: Any) -> Message:
    """Validates an untrusted message. Raises ValidationError if it is malformed."""
    return _message_adapter.validate_python(raw)


def new_message(
        message_type: type[M],
        *,
        source: str,
        correlation_id: str,
        causation_id: str | None,
        data: BaseModel,
) -> M:
    # `type` and `data` are declared on the subclasses, which a bound TypeVar can't see.
    fields: dict[str, Any] = {
        "message_id": str(uuid4()),
        "type": message_type.__name__,
        "schema_version": 1,
        "occurred_at": datetime.now(UTC),
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "source": source,
        "data": data,
    }
    return message_type(**fields)


def to_wire(message: Envelope) -> dict[str, Any]:
    """The JSON-compatible form used in the outbox, SQS and EventBridge."""
    return message.model_dump(mode="json", by_alias=True)
