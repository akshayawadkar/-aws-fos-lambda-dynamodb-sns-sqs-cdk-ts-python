from typing import Any

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk.aws_apigatewayv2 import HttpMethod
from constructs import Construct

from .components import OutboxPublisher, consume_queue, http_api, service_function, service_table, shared_layer
from .messaging_stack import MessagingStack


class OrderStack(Stack):
    def __init__(self, scope: Construct, id: str, *, messaging: MessagingStack, **kwargs: Any) -> None:
        super().__init__(scope, id, **kwargs)
        source = "services/order"

        table = service_table(self, "Table")
        # Sparse: only in-progress orders carry sweepPk, so the sweeper never scans finished ones.
        table.add_global_secondary_index(
            index_name="ActiveOrders",
            partition_key=dynamodb.Attribute(name="sweepPk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sweepSk", type=dynamodb.AttributeType.STRING),
        )
        layer = shared_layer(self, "SharedLayer")
        environment = {"TABLE_NAME": table.table_name}

        OutboxPublisher(
            self,
            "Outbox",
            table=table,
            event_bus=messaging.event_bus,
            layer=layer,
            command_queues={
                "PAYMENT_COMMANDS_QUEUE_URL": messaging.payment_commands,
                "RESTAURANT_COMMANDS_QUEUE_URL": messaging.restaurant_commands,
            },
        )

        orchestrator = service_function(self, "Orchestrator", source, "fos_order.events.handler", layer, environment)
        table.grant_read_write_data(orchestrator)
        consume_queue(orchestrator, messaging.order_events)

        api_fn = service_function(
            self,
            "ApiFunction",
            source,
            "fos_order.api.handler",
            layer,
            {**environment, "MAX_ITEMS_PER_ORDER": "10"},
        )
        table.grant_read_write_data(api_fn)
        api = http_api(
            self,
            "Api",
            api_fn,
            [
                (HttpMethod.POST, "/orders"),
                (HttpMethod.GET, "/orders/{orderId}"),
            ],
        )
        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
