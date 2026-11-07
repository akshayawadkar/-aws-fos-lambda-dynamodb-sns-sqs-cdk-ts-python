# FOS – Deployed AWS Resources

What is actually live in AWS, phase by phase. For the planned design of every stack, see [aws-resources.html](aws-resources.html).

| | |
|---|---|
| Account | `627626160133` |
| Region | `us-east-2` (Ohio) |
| Prerequisite | `CDKToolkit` bootstrap stack: `CREATE_COMPLETE` |

## Deployment progress

| Phase | Stack | Status | Deployed | Resources |
|---|---|---|---|---|
| 1 | `FosMessaging` | ✅ `CREATE_COMPLETE` | 2026-11-07 | 13 |
| 2 | `FosPayment` | ⏳ not deployed | – | – |
| 3 | `FosRestaurant` | ⏳ not deployed | – | – |
| 4 | `FosOrder` | ⏳ not deployed | – | – |

---

## Phase 1 – `FosMessaging`

The shared messaging layer. It has no compute and no data stores. It holds the bus and queues that the three service stacks will read from and write to.

```
                    ┌──────────────────────────────┐
  fos.payment ────▶ │ EventBus                     │
  fos.restaurant ─▶ │ FosMessagingEventBusE07891D3 │
                    └──────────────┬───────────────┘
                                   │ Rule EventsToOrder
                                   │ source ∈ {fos.payment, fos.restaurant}
                                   ▼
                    OrderEvents ──(5 failed receives)──▶ OrderEventsDlq ──▶ Alarm

  (Order service) ─▶ PaymentCommands ────(5)────▶ PaymentCommandsDlq ────▶ Alarm
  (Order service) ─▶ RestaurantCommands ─(5)────▶ RestaurantCommandsDlq ─▶ Alarm
```

Nothing produces to or consumes from these queues yet. Producers and consumers arrive with phases 2–4.

### Resources (13)

| # | Type | Logical ID | Physical name |
|---|---|---|---|
| 1 | `AWS::Events::EventBus` | `EventBus7B8748AA` | `FosMessagingEventBusE07891D3` |
| 2 | `AWS::Events::Rule` | `EventsToOrder9DDE3F9F` | `FosMessaging-EventsToOrder9DDE3F9F-gAP8X0oziiEB` |
| 3 | `AWS::SQS::Queue` | `PaymentCommandsB260C99D` | `FosMessaging-PaymentCommandsB260C99D-P3ux5hc0qlSB` |
| 4 | `AWS::SQS::Queue` | `PaymentCommandsDlq8B872727` | `FosMessaging-PaymentCommandsDlq8B872727-qfTZXiniuNtG` |
| 5 | `AWS::CloudWatch::Alarm` | `PaymentCommandsDlqAlarm8B17EBD3` | `FosMessaging-PaymentCommandsDlqAlarm8B17EBD3-p3IvhJ1lkP7B` |
| 6 | `AWS::SQS::Queue` | `RestaurantCommands1E80532F` | `FosMessaging-RestaurantCommands1E80532F-sXAmvPGSDiaM` |
| 7 | `AWS::SQS::Queue` | `RestaurantCommandsDlqD8D305AF` | `FosMessaging-RestaurantCommandsDlqD8D305AF-YQVxq23yu4rY` |
| 8 | `AWS::CloudWatch::Alarm` | `RestaurantCommandsDlqAlarm7881F3A0` | `FosMessaging-RestaurantCommandsDlqAlarm7881F3A0-YEgKJmkFYNno` |
| 9 | `AWS::SQS::Queue` | `OrderEvents91E7078A` | `FosMessaging-OrderEvents91E7078A-Cbe0VgfxUXfs` |
| 10 | `AWS::SQS::Queue` | `OrderEventsDlq56EEDE37` | `FosMessaging-OrderEventsDlq56EEDE37-9Yqz7J43A6aL` |
| 11 | `AWS::SQS::QueuePolicy` | `OrderEventsPolicy5698F269` | `FosMessaging-OrderEventsPolicy5698F269-CJnUrKATJEcB` |
| 12 | `AWS::CloudWatch::Alarm` | `OrderEventsDlqAlarmB5284176` | `FosMessaging-OrderEventsDlqAlarmB5284176-J1yomW5vhaAi` |
| 13 | `AWS::CDK::Metadata` | `CDKMetadata` | CDK version telemetry only, not a billable resource |

### EventBridge

**Event bus**: `FosMessagingEventBusE07891D3`. This is a custom bus, separate from the account's `default` bus. Service stacks publish domain events here through their outbox publisher Lambdas. It has no archive, no replay and no resource policy, so only principals in this account with IAM permission can put events on it.

**Rule** `EventsToOrder`:

| Setting | Value |
|---|---|
| State | `ENABLED` |
| Event pattern | `{"source": ["fos.payment", "fos.restaurant"]}` |
| Target | `OrderEvents` queue (`Target0`) |
| Retry policy / target DLQ | none set, so EventBridge defaults apply (retries for 24 h, then drops the event) |

### SQS queues

All six queues use SSE-SQS encryption (AWS-managed keys), standard (not FIFO) ordering, 0 s delivery delay, short polling at the queue level (the Lambda event source will long-poll), and a 1 MiB max message size.

| Queue | Role | Visibility timeout | Retention | Redrive |
|---|---|---|---|---|
| `PaymentCommands` | Order → Payment commands | 180 s (6 × 30 s Lambda timeout) | 4 days | → `PaymentCommandsDlq` after 5 receives |
| `PaymentCommandsDlq` | dead letters | 30 s | 14 days | – |
| `RestaurantCommands` | Order → Restaurant commands | 180 s | 4 days | → `RestaurantCommandsDlq` after 5 receives |
| `RestaurantCommandsDlq` | dead letters | 30 s | 14 days | – |
| `OrderEvents` | Payment/Restaurant events → Order | 180 s | 4 days | → `OrderEventsDlq` after 5 receives |
| `OrderEventsDlq` | dead letters | 30 s | 14 days | – |

Each DLQ keeps messages 10 days longer than its main queue, so a message that is moved late in its life still leaves time to investigate it.

**Queue policy** on `OrderEvents`. This is the only resource-based policy in the stack:

| Effect | Principal | Actions | Condition |
|---|---|---|---|
| Allow | `events.amazonaws.com` | `sqs:SendMessage`, `sqs:GetQueueAttributes`, `sqs:GetQueueUrl` | `aws:SourceArn` = the `EventsToOrder` rule ARN |

The `SourceArn` condition means only that one rule can write to the queue. Other rules or buses cannot.

### CloudWatch alarms

There are three identical alarms, one per DLQ:

| Setting | Value |
|---|---|
| Metric | `AWS/SQS` `ApproximateNumberOfMessagesVisible` on the DLQ |
| Statistic / period | Maximum / 60 s |
| Condition | ≥ 1 for 1 evaluation period |
| Missing data | `notBreaching` |
| Actions | **none** |
| Current state | `OK` (all three) |

### Cross-stack contract

The stack declares no `CfnOutput`s. Later stacks receive `messaging.event_bus`, `.payment_commands`, `.restaurant_commands` and `.order_events` as Python objects. CDK turns these into cross-stack references when they're consumed, using `"@aws-cdk/core:defaultCrossStackReferences": "weak"` in `cdk.json`. Expect `FosMessaging` to gain exports or SSM-backed references on its next deploy after phase 2 is enabled.

### Removal behaviour

All six queues have `DeletionPolicy: Delete`. The bus, rule, policy and alarms use the CloudFormation default (delete). `cdk destroy FosMessaging` removes everything, including any messages still in the queues. Destroy the service stacks first (Order → Restaurant → Payment → Messaging).

### Idle cost

About **$0.30/month**: three standard-resolution alarms at $0.10 each. A custom event bus, idle SQS queues and SSE-SQS are free when unused. Lambda event-source polling in later phases adds a small number of SQS requests.

### Observations / follow-ups

- **The alarms notify no one.** `AlarmActions` is empty, so a message landing in a DLQ only changes the alarm state in the console. Add an SNS topic with an email subscription if you want to be paged.
- **The rule target has no DLQ.** If EventBridge can't deliver to `OrderEvents`, it retries for 24 h and then silently drops the event. Consider `dead_letter_queue=` and `retry_attempts=` on `targets.SqsQueue(...)`.
- **There's no event archive on the bus.** Enabling one would let you replay events while debugging later phases.

### Verify it yourself

```powershell
aws cloudformation list-stack-resources --stack-name FosMessaging --region us-east-2 --output table
aws events describe-rule --event-bus-name FosMessagingEventBusE07891D3 --name FosMessaging-EventsToOrder9DDE3F9F-gAP8X0oziiEB --region us-east-2

# Smoke test: an event with source fos.payment should land in OrderEvents
# (PowerShell mangles inline JSON quotes, so pass the entry as a file)
'[{"EventBusName":"FosMessagingEventBusE07891D3","Source":"fos.payment","DetailType":"Test","Detail":"{}"}]' | Out-File -Encoding ascii test-event.json
aws events put-events --region us-east-2 --entries file://test-event.json
aws sqs receive-message --region us-east-2 --queue-url https://sqs.us-east-2.amazonaws.com/627626160133/FosMessaging-OrderEvents91E7078A-Cbe0VgfxUXfs
```

---

## Phase 2 – `FosPayment`

_Not deployed yet._

## Phase 3 – `FosRestaurant`

_Not deployed yet._

## Phase 4 – `FosOrder`

_Not deployed yet._
