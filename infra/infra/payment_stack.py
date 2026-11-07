from typing import Any

from aws_cdk import CfnOutput, Stack
from aws_cdk.aws_apigatewayv2 import HttpMethod
from constructs import Construct

from .components import OutboxPublisher, consume_queue, http_api, service_function, service_table, shared_layer
from .messaging_stack import MessagingStack


class PaymentStack(Stack):
    def __init__(self, scope: Construct, id: str, *, messaging: MessagingStack, **kwargs: Any) -> None:
        super().__init__(scope, id, **kwargs)
        source = "services/payment"

        table = service_table(self, "Table")
        layer = shared_layer(self, "SharedLayer")
        environment = {"TABLE_NAME": table.table_name}

        OutboxPublisher(self, "Outbox", table=table, event_bus=messaging.event_bus, layer=layer)

        commands = service_function(self, "Commands", source, "fos_payment.commands.handler", layer, environment)
        table.grant_read_write_data(commands)
        consume_queue(commands, messaging.payment_commands)

        api_fn = service_function(self, "ApiFunction", source, "fos_payment.api.handler", layer, environment)
        table.grant_read_write_data(api_fn)
        api = http_api(
            self,
            "Api",
            api_fn,
            [
                (HttpMethod.POST, "/accounts"),
                (HttpMethod.GET, "/accounts/{accountId}"),
                (HttpMethod.POST, "/accounts/{accountId}/credit"),
                (HttpMethod.GET, "/accounts/{accountId}/ledger"),
            ],
        )
        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
