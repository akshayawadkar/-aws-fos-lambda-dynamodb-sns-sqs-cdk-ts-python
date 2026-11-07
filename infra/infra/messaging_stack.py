from typing import Any

from aws_cdk import Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from constructs import Construct

from .components import queue_with_dlq


class MessagingStack(Stack):
    """The bus and queues are shared, so they live apart from the services that use them."""

    def __init__(self, scope: Construct, id: str, **kwargs: Any) -> None:
        super().__init__(scope, id, **kwargs)

        self.event_bus = events.EventBus(self, "EventBus")
        self.payment_commands, _ = queue_with_dlq(self, "PaymentCommands")
        self.restaurant_commands, _ = queue_with_dlq(self, "RestaurantCommands")
        self.order_events, _ = queue_with_dlq(self, "OrderEvents")

        events.Rule(
            self,
            "EventsToOrder",
            event_bus=self.event_bus,
            event_pattern=events.EventPattern(source=["fos.payment", "fos.restaurant"]),
            targets=[targets.SqsQueue(self.order_events)],
        )
