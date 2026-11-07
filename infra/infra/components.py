import shutil
import subprocess
from pathlib import Path

from typing import Any
import sys
import jsii
from aws_cdk import Duration, ILocalBundling, BundlingOptions, RemovalPolicies, RemovalPolicy
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_dynamodb as dynamo
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_events as events
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from aws_cdk.aws_lambda_event_sources import DynamoEventSource, SqsDlq, SqsEventSource
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
LAMBDA_TIMEOUT = Duration.seconds(30)
RUNTIME = lambda_.Runtime.PYTHON_3_14
ARCHITECTURE = lambda_.Architecture.ARM_64
PYTHON_VERSION = "3.14"
SOURCE_EXCLUDES = ["tests", "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache"]
SHARED_DIR = REPO_ROOT / "packages" / "shared"


@jsii.implements(ILocalBundling)
class _LocalPipBundling:
    """Installs Linux ARM wheels with this machine's pip, so Docker is only a fallback."""

    def try_bundle(self, output_dir: str, *, image: Any = None, **_: Any) -> bool:
        target = Path(output_dir) / "python"
        try:
            pip = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install",
                    "-r", str(SHARED_DIR / "requirements.txt"),
                    "--target", str(target),
                    "--platform", "manylinux2014_aarch64",
                    "--platform", "manylinux_2_28_aarch64",
                    "--implementation", "cp",
                    "--python-version", PYTHON_VERSION,
                    "--only-binary=:all:",
                    "--quiet",
                ],
                check=False
            )
        except OSError:
            return False
        if pip.returncode != 0:
            return False

        shutil.copytree(
            SHARED_DIR / "fos_shared",
            target / "fos_shared",
            ignore=shutil.ignore_patterns("__pycache", "*.pyc"),
            dirs_exist_ok=True
        )
        return True


def shared_layer(scope: Construct, id: str) -> lambda_.LayerVersion:
    return lambda_.LayerVersion(
        scope,
        id,
        compatible_runtimes=[RUNTIME],
        compatible_architectures=[ARCHITECTURE],
        code=lambda_.Code.from_asset(
            str(SHARED_DIR),
            exclude=SOURCE_EXCLUDES,
            bundling=BundlingOptions(
                image=RUNTIME.bundling_image,
                platform="linux/arm64",
                command=[
                    "bash",
                    "-c",
                    "pip install -r requirements.txt --only-binary=:all: --target /asset-output/python"
                    " && cp -r fos_shared /asset-output/python/",
                ],
                local=_LocalPipBundling(),
            )
        )
    )


def service_function(
        scope: Construct,
        id: str,
        source_dir: str,
        handler: str,
        layer: lambda_.ILayerVersion,
        environment: dict[str, str]
) -> lambda_.Function:
    """`source_dir` is relative to the repo root, and `handler` is a dotted module path."""
    return lambda_.Function(
        scope,
        id,
        code=lambda_.Code.from_asset(str(REPO_ROOT / source_dir), exclude=SOURCE_EXCLUDES),
        handler=handler,
        runtime=RUNTIME,
        architecture=ARCHITECTURE,
        memory_size=256,
        timeout=LAMBDA_TIMEOUT,
        layers=[layer],
        environment=environment
    )


def service_table(scope: Construct, id: str) -> dynamo.Table:
    return dynamo.Table(
        scope,
        id,
        partition_key=dynamo.Attribute(name="PK", type=dynamo.AttributeType.STRING),
        sort_key=dynamo.Attribute(name="SK", type=dynamo.AttributeType.STRING),
        billing_mode=dynamo.BillingMode.PAY_PER_REQUEST,
        stream=dynamo.StreamViewType.NEW_IMAGE,
        time_to_live_attribute="expiresAt",
        removal_policy=RemovalPolicy.DESTROY  # A learning stack: `cdk destroy` should leave nothing behind.
    )


def dlq_alarm(scope: Construct, id: str, dlq: sqs.Queue) -> cloudwatch.Alarm:
    return cloudwatch.Alarm(
        scope,
        id,
        metric=dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(1), statistic="Maximum"),
        threshold=1,
        evaluation_periods=1,
        treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        alarm_description="Messages reached a dead-letter queue and need investigation"
    )


def queue_with_dlq(scope: Construct, id: str) -> tuple[sqs.Queue, sqs.Queue]:
    """A queue and its DLQ, with an alarm that fires as soon as anything lands in the DLQ."""
    dlq = sqs.Queue(
        scope,
        f"{id}Dlq",
        retention_period=Duration.days(14),
        encryption=sqs.QueueEncryption.SQS_MANAGED
    )

    queue = sqs.Queue(
        scope,
        id,
        # AWS recommends at least six times the consuming function's timeout.
        visibility_timeout=Duration.seconds(LAMBDA_TIMEOUT.to_seconds() * 6),
        encryption=sqs.QueueEncryption.SQS_MANAGED,
        dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=5)
    )

    dlq_alarm(scope, f"{id}DlqAlarm", dlq)
    return queue, dlq


class OutboxPublisher(Construct):
    """Streams new outbox rows from a service's table to EventBridge or a command queue."""

    def __init__(self,
                 scope: Construct,
                 id: str,
                 *,
                 table: dynamo.Table,
                 event_bus: events.IEventBus,
                 layer: lambda_.ILayerVersion,
                 command_queues: dict[str, sqs.IQueue] | None = None
                 ) -> None:
        super().__init__(scope, id)
        command_queues = command_queues or {}

        fn = service_function(
            self,
            "Function",
            "packages/shared",
            "fos_share.outbox_publisher.handler",
            layer,
            {
                "EVENT_BUS_NAME": event_bus.event_bus_name,
                **{env: queue.queue_url for env, queue in command_queues.items()},
            }
        )
        event_bus.grant_put_events_to(fn)
        for queue in command_queues.values():
            queue.grant_send_messages(fn)

        failures = sqs.Queue(
            self,
            "FailureQueue",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED
        )

        dlq_alarm(self, "FailureQueue", failures)

        fn.add_event_source(
            DynamoEventSource(
                table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
                bisect_batch_on_error=True,
                retry_attempts=10,
                report_batch_item_failures=True,
                on_failure=SqsDlq(failures),
                filters=[
                    lambda_.FilterCriteria.filter(
                        {
                            "eventName": lambda_.FilterRule.is_equal("INSERT"),
                            "dynamodb": {"Keys": {"PK": {"S": lambda_.FilterRule.begins_with("OUTBOX#")}}},
                        }
                    )
                ]
            )
        )


def consume_queue(fn: lambda_.Function, queue: sqs.IQueue) -> None:
    """Consumes a queue with partial batch responses, so one failure doesn't redeliver the batch."""
    fn.add_event_source(SqsEventSource(queue, batch_size=10, report_batch_item_failures=True))


def http_api(
        scope: Construct,
        id: str,
        fn: lambda_.IFunction,
        routes: list[tuple[apigw.HttpMethod, str]]
) -> apigw.HttpApi:
    api = apigw.HttpApi(scope, id)
    integration = HttpLambdaIntegration(f"{id}Integration", fn)
    for method, route_path in routes:
        api.add_routes(path=route_path, methods=[method], integration=integration)
    return api
