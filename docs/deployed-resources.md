# FOS – Deployed AWS Resources

What is actually live in AWS, phase by phase. For the planned design of every stack, see [aws-resources.html](aws-resources.html). To call the APIs, import [postman/fos.postman_collection.json](postman/fos.postman_collection.json).

| | |
|---|---|
| Account | `627626160133` |
| Region | `us-east-2` (Ohio) |
| Prerequisite | `CDKToolkit` bootstrap stack: `CREATE_COMPLETE` |
| Last checked | 2026-11-08 |

## Deployment progress

| Phase | Stack | Status | Created (UTC) | Resources | API |
|---|---|---|---|---|---|
| 1 | `FosMessaging` | ✅ `UPDATE_COMPLETE` | 2026-11-08 15:23 | 13 | – |
| 2 | `FosPayment` | ✅ `UPDATE_COMPLETE` | 2026-11-08 15:25 | 30 | `https://jqq85fsgqd.execute-api.us-east-2.amazonaws.com` |
| 3 | `FosRestaurant` | ✅ `CREATE_COMPLETE` | 2026-11-08 17:15 | 30 | `https://ov5aeaff1b.execute-api.us-east-2.amazonaws.com` |
| 4 | `FosOrder` | ✅ `CREATE_COMPLETE` | 2026-11-08 17:29 | 26 | `https://jjdvbcmwsc.execute-api.us-east-2.amazonaws.com` |

All four phases are deployed.

> ⚠️ **Action needed: redeploy `FosPayment` and `FosRestaurant`.** Their outbox publisher Lambdas still run the handler `fos_share.outbox_publisher.handler` (missing `d`). `FosOrder` was deployed after the fix and is correct. Until the other two are redeployed, **every order stops at `PAYMENT_PENDING`**, because `PaymentCompleted` can't be published, and Order's product catalog stays empty, because `ProductChanged` can't be published either. See [Known issues](#known-issues).

```
                           FosOrder  ◀── HTTP  POST /orders, GET /orders/{id}
                    ┌───────────────────────────────┐
                    │ Api λ ── order + DebitPayment ─▶ Table ─stream─▶ Outbox λ ──┐ commands
                    │ Orchestrator λ ◀── OrderEvents │                          │
                    └────────────▲──────────────────┘                          │
                                 │                        ┌────────────────────┴───────────┐
                                 │                        ▼                                ▼
                        ┌────────┴────────┐       PaymentCommands                 RestaurantCommands
                        │   OrderEvents   │               │                                │
                        └────────▲────────┘               ▼                                ▼
                                 │ Rule EventsToOrder   FosPayment                     FosRestaurant
                                 │ (fos.payment,        Commands λ ─▶ Table            Commands λ ─▶ Table
                        ┌────────┴─────────┐                  │stream                          │stream
                        │ EventBus (FOS)   │◀── Outbox λ ◀────┘          Outbox λ ◀─────────────┘
                        └──────────────────┘◀──────────────────────────────┘   (also ProductChanged from the API)
```

---

## Phase 1 – `FosMessaging`

The shared messaging layer: the event bus and the three command/event queues. It has no compute and no data.

The stack was destroyed and re-created on 2026-11-08, so every physical name below differs from the first deployment on 2026-11-07. It was updated at 17:15 and 17:28 UTC to publish outputs for the service stacks (see [Cross-stack references](#cross-stack-references)).

### Resources (13)

| # | Type | Logical ID | Physical name |
|---|---|---|---|
| 1 | `AWS::Events::EventBus` | `EventBus7B8748AA` | `FosMessagingEventBusE07891D3` |
| 2 | `AWS::Events::Rule` | `EventsToOrder9DDE3F9F` | `FosMessaging-EventsToOrder9DDE3F9F-Gp9pUv0n9kVR` |
| 3 | `AWS::SQS::Queue` | `PaymentCommandsB260C99D` | `FosMessaging-PaymentCommandsB260C99D-vadsNJLQ8bNO` |
| 4 | `AWS::SQS::Queue` | `PaymentCommandsDlq8B872727` | `FosMessaging-PaymentCommandsDlq8B872727-Pb9lUFDaX39K` |
| 5 | `AWS::CloudWatch::Alarm` | `PaymentCommandsDlqAlarm8B17EBD3` | `FosMessaging-PaymentCommandsDlqAlarm8B17EBD3-zclimagzKGvB` |
| 6 | `AWS::SQS::Queue` | `RestaurantCommands1E80532F` | `FosMessaging-RestaurantCommands1E80532F-JyJSWs8KJWLr` |
| 7 | `AWS::SQS::Queue` | `RestaurantCommandsDlqD8D305AF` | `FosMessaging-RestaurantCommandsDlqD8D305AF-Lbb6lvD36B6A` |
| 8 | `AWS::CloudWatch::Alarm` | `RestaurantCommandsDlqAlarm7881F3A0` | `FosMessaging-RestaurantCommandsDlqAlarm7881F3A0-rliMvXVYMw9l` |
| 9 | `AWS::SQS::Queue` | `OrderEvents91E7078A` | `FosMessaging-OrderEvents91E7078A-Getq0gVhVdW3` |
| 10 | `AWS::SQS::Queue` | `OrderEventsDlq56EEDE37` | `FosMessaging-OrderEventsDlq56EEDE37-gKIUVttJW0Uh` |
| 11 | `AWS::SQS::QueuePolicy` | `OrderEventsPolicy5698F269` | `FosMessaging-OrderEventsPolicy5698F269-sYuByDJ27BAW` |
| 12 | `AWS::CloudWatch::Alarm` | `OrderEventsDlqAlarmB5284176` | `FosMessaging-OrderEventsDlqAlarmB5284176-EJsrfxCQelXi` |
| 13 | `AWS::CDK::Metadata` | `CDKMetadata` | CDK version telemetry only, not a billable resource |

### EventBridge

**Event bus** `FosMessagingEventBusE07891D3`. A custom bus (the name is stable across re-creation because CDK derives it from the stack and construct IDs). The two outbox publishers put events on it. It has no archive, no replay and no resource policy.

**Rule** `EventsToOrder`:

| Setting | Value |
|---|---|
| State | `ENABLED` |
| Event pattern | `{"source": ["fos.payment", "fos.restaurant"]}` |
| Target | `OrderEvents` queue |
| Retry policy / target DLQ | none set, so EventBridge defaults apply (retries for 24 h, then drops the event) |

### SQS queues

All six use SSE-SQS encryption, standard (not FIFO) ordering and a 1 MiB max message size.

| Queue | Producer → consumer | Visibility timeout | Retention | Redrive |
|---|---|---|---|---|
| `PaymentCommands` | FosOrder Outbox λ → **FosPayment Commands λ** | 180 s (6 × 30 s Lambda timeout) | 4 days | → `PaymentCommandsDlq` after 5 receives |
| `PaymentCommandsDlq` | dead letters | 30 s | 14 days | – |
| `RestaurantCommands` | FosOrder Outbox λ → **FosRestaurant Commands λ** | 180 s | 4 days | → `RestaurantCommandsDlq` after 5 receives |
| `RestaurantCommandsDlq` | dead letters | 30 s | 14 days | – |
| `OrderEvents` | EventBridge rule → **FosOrder Orchestrator λ** | 180 s | 4 days | → `OrderEventsDlq` after 5 receives |
| `OrderEventsDlq` | dead letters | 30 s | 14 days | – |

Every queue now has a consumer. All nine FOS queues (these six plus the three outbox failure queues) were empty when checked.

**Queue policy** on `OrderEvents`: allows `events.amazonaws.com` to `SendMessage`, `GetQueueAttributes` and `GetQueueUrl`, only when `aws:SourceArn` is the `EventsToOrder` rule.

### CloudWatch alarms

One per DLQ: `ApproximateNumberOfMessagesVisible` Maximum over 60 s, ≥ 1 for 1 period, missing data `notBreaching`, **no actions**. All three are `OK`.

### Cross-stack references

`cdk.json` sets `"@aws-cdk/core:defaultCrossStackReferences": "weak"`. Instead of CloudFormation `Export`/`ImportValue` (which would lock `FosMessaging` while anything imports it), CDK publishes plain stack outputs, and the consuming stacks are deployed with those values filled in. `list-exports` shows no FOS exports, as expected.

| Output | Value | Used by |
|---|---|---|
| `PublishOutputRefEventBus7B8748AA72D03D9D` | `FosMessagingEventBusE07891D3` | all three outbox λ (`EVENT_BUS_NAME`) |
| `PublishOutputFnGetAttEventBus7B8748AAArn0C0C3C6A` | event bus ARN | all three outbox λ IAM policies (`events:PutEvents`) |
| `PublishOutputFnGetAttPaymentCommandsB260C99DArn0A60171E` | `PaymentCommands` ARN | FosPayment event source mapping and IAM policy; FosOrder outbox `sqs:SendMessage` |
| `PublishOutputFnGetAttRestaurantCommands1E80532FArn7E769F7C` | `RestaurantCommands` ARN | FosRestaurant event source mapping and IAM policy; FosOrder outbox `sqs:SendMessage` |
| `PublishOutputRefPaymentCommandsB260C99DCB380E11` | `PaymentCommands` URL | FosOrder outbox λ (`PAYMENT_COMMANDS_QUEUE_URL`) |
| `PublishOutputRefRestaurantCommands1E80532F2D17AF9D` | `RestaurantCommands` URL | FosOrder outbox λ (`RESTAURANT_COMMANDS_QUEUE_URL`) |
| `PublishOutputFnGetAttOrderEvents91E7078AArnB189768C` | `OrderEvents` ARN | FosOrder event source mapping and IAM policy |

The trade-off: nothing stops you from replacing `FosMessaging` while the service stacks still point at the old ARNs. If you destroy and redeploy `FosMessaging` again, redeploy every service stack after it.

---

## Phase 2 – `FosPayment`

Customer accounts and balances. It debits and refunds orders on command and reports the result as events.

### Resources (30)

| Group | Type | Logical ID | Physical name / ID |
|---|---|---|---|
| Data | `AWS::DynamoDB::Table` | `TableCD117FA1` | `FosPayment-TableCD117FA1-MMBOOLJYB9GZ` |
| Layer | `AWS::Lambda::LayerVersion` | `SharedLayer27DFABF0` | `SharedLayer27DFABF0:1` |
| API λ | `AWS::Lambda::Function` | `ApiFunctionCE271BD4` | `FosPayment-ApiFunctionCE271BD4-l6epHMitzyDP` |
| | `AWS::IAM::Role` / `Policy` | `ApiFunctionServiceRole…` | `FosPayment-ApiFunctionServiceRole52B9747B-aCSLnE4iyuku` |
| | `AWS::Logs::LogGroup` | `ApiFunctionLogGroupB51C7C52` | `/aws/lambda/FosPayment-ApiFunctionCE271BD4-l6epHMitzyDP` |
| Commands λ | `AWS::Lambda::Function` | `Commands361DBF70` | `FosPayment-Commands361DBF70-PNkquJkpEyh0` |
| | `AWS::IAM::Role` / `Policy` | `CommandsServiceRole…` | `FosPayment-CommandsServiceRole9F1B8F78-rf7CHXYp9eCS` |
| | `AWS::Logs::LogGroup` | `CommandsLogGroupD50D6164` | `/aws/lambda/FosPayment-Commands361DBF70-PNkquJkpEyh0` |
| | `AWS::Lambda::EventSourceMapping` | `CommandsSqsEventSource…` | `6b1ae91e-dc07-49d8-91e1-848acd754a11` |
| Outbox λ | `AWS::Lambda::Function` | `OutboxFunctionAB2D95BD` | `FosPayment-OutboxFunctionAB2D95BD-biKGrE6M20sJ` |
| | `AWS::IAM::Role` / `Policy` | `OutboxFunctionServiceRole…` | `FosPayment-OutboxFunctionServiceRole3524289E-pOqDGqwJ3TTn` |
| | `AWS::Logs::LogGroup` | `OutboxFunctionLogGroupCD15F1B8` | `/aws/lambda/FosPayment-OutboxFunctionAB2D95BD-biKGrE6M20sJ` |
| | `AWS::Lambda::EventSourceMapping` | `OutboxFunctionDynamoDBEventSource…` | `ede047e1-8b16-4e3a-911d-00ede8a6aa0c` |
| | `AWS::SQS::Queue` | `OutboxFailureQueueF968CAFD` | `FosPayment-OutboxFailureQueueF968CAFD-7iljKHS7uW3s` |
| | `AWS::CloudWatch::Alarm` | `OutboxFailureQueueAlarmB4029836` | `FosPayment-OutboxFailureQueueAlarmB4029836-rZ9ZIu7lDQPy` |
| HTTP API | `AWS::ApiGatewayV2::Api` | `ApiF70053CD` | `jqq85fsgqd` |
| | `AWS::ApiGatewayV2::Stage` | `ApiDefaultStage189A7074` | `$default` (auto-deploy) |
| | `AWS::ApiGatewayV2::Integration` | `ApiPOSTaccountsApiIntegration…` | `cwmk4hu` (one Lambda proxy integration shared by all routes) |
| | `AWS::ApiGatewayV2::Route` × 4 | `ApiPOSTaccounts…`, … | see routes below |
| | `AWS::Lambda::Permission` × 4 | `Api…IntegrationPermission…` | one per route |
| | `AWS::CDK::Metadata` | `CDKMetadata` | – |

The "Role / Policy" rows are two resources each, so there are 30 in total.

### Lambda functions

All three: Python 3.14, arm64, 256 MB, 30 s timeout, layer `SharedLayer27DFABF0:1`.

| Function | Handler | Trigger | Environment |
|---|---|---|---|
| Api | `fos_payment.api.handler` | HTTP API (all 4 routes) | `TABLE_NAME` |
| Commands | `fos_payment.commands.handler` | `PaymentCommands` queue: batch 10, no batching window, `ReportBatchItemFailures` | `TABLE_NAME` |
| Outbox | ⚠️ `fos_share.outbox_publisher.handler` | table stream: see below | `EVENT_BUS_NAME` |

**Outbox event source mapping** (same in both service stacks): starting position `LATEST`, batch 10, `ReportBatchItemFailures`, bisect on error, 10 retries, then the batch's details go to `OutboxFailureQueue`. A filter only passes `INSERT`s of keys starting with `OUTBOX#`, so other table writes never invoke it. Last processing result: *No records processed*.

**Outbox failure queue**: SSE-SQS, 14-day retention, 30 s visibility, alarm when ≥ 1 message (no actions), currently empty and `OK`. It receives a *pointer* to the failed stream batch, not the event itself. The outbox row stays in the table until its 7-day TTL expires, so a failed event can still be found and republished.

### DynamoDB table

`FosPayment-TableCD117FA1-MMBOOLJYB9GZ`: `PK`/`SK` strings, on-demand billing, stream `NEW_IMAGE`, TTL on `expiresAt`, AWS-owned encryption, no GSIs, deletion protection off, `RemovalPolicy.DESTROY`.

| PK | SK | Holds |
|---|---|---|
| `ACCOUNT#<accountId>` | `META` | name, `status` (`ACTIVE`), `currency` (`USD`), `balanceMinor`, timestamps |
| `ACCOUNT#<accountId>` | `LEDGER#<ulid>` | append-only ledger entry (`CREDIT`/`DEBIT`, `amountMinor`, `orderId?`), time-ordered by ULID |
| `ACCOUNT#<accountId>` | `CREDITREF#<referenceId>` | makes API top-ups idempotent |
| `PAYMENT#<orderId>` | `DEBIT` | debit outcome for an order (`COMPLETED` / `FAILED` + reason) |
| `PAYMENT#<orderId>` | `REFUND` | refund outcome (`REFUNDED` / `NOTHING_TO_REFUND`); blocks a late debit |
| `INBOX#<messageId>` | `META` | processed-command marker, 7-day TTL |
| `OUTBOX#<messageId>` | `META` | event waiting to be published, 7-day TTL |

### HTTP API

HTTP API (payload v2), no authorizer, no CORS, no access logs, no throttling overrides.

| Route | Does |
|---|---|
| `POST /accounts` | `{"name"}` → 201, ACTIVE USD account, balance 0 |
| `GET /accounts/{accountId}` | 200 account (consistent read) / 404 |
| `POST /accounts/{accountId}/credit` | `{"amountMinor", "referenceId"}` → 201 applied / 200 `duplicate: true` / 404 |
| `GET /accounts/{accountId}/ledger` | 200, up to 50 entries, newest first |

### Messages

| Direction | Type | Via |
|---|---|---|
| in | `DebitPayment`, `RefundPayment` | `PaymentCommands` queue |
| out | `PaymentCompleted`, `PaymentFailed` (`INSUFFICIENT_FUNDS`, `ACCOUNT_NOT_FOUND`, `ACCOUNT_INACTIVE`, `REFUNDED_BEFORE_DEBIT`), `PaymentRefunded` | outbox → bus (`fos.payment`) → `OrderEvents` |

The API writes no events. Only the commands handler does.

### IAM (least privilege, generated by CDK grants)

| Role | Can |
|---|---|
| Api, Commands | read/write this table only |
| Commands | receive/delete on `PaymentCommands` |
| Outbox | read this table's stream, `events:PutEvents` on the FOS bus, send to its failure queue |

---

## Phase 3 – `FosRestaurant`

Restaurants and their menus. It accepts or rejects orders on command and announces product changes.

### Resources (30)

Same shape as `FosPayment`: table, shared layer version, three Lambdas with role, policy and log group, two event source mappings, outbox failure queue and alarm, and an HTTP API with 4 routes and 4 permissions.

| Group | Type | Physical name / ID |
|---|---|---|
| Data | `AWS::DynamoDB::Table` | `FosRestaurant-TableCD117FA1-1LXZ94ZDH15KP` |
| Layer | `AWS::Lambda::LayerVersion` | `SharedLayer27DFABF0:2` |
| API λ | Function | `FosRestaurant-ApiFunctionCE271BD4-zkwqQCKjFzQk` |
| Commands λ | Function | `FosRestaurant-Commands361DBF70-ggqajG1PkfDo` |
| | Event source mapping | `00f2d0a5-72ec-4dde-abcd-a3c495a74964` (`RestaurantCommands`, batch 10, `ReportBatchItemFailures`) |
| Outbox λ | Function | `FosRestaurant-OutboxFunctionAB2D95BD-9hmTojWMS2t1` (⚠️ same handler typo) |
| | Event source mapping | `20d64480-8441-48a3-9f45-281362d0fb04` (stream, same settings as Payment) |
| | Failure queue | `FosRestaurant-OutboxFailureQueueF968CAFD-uuyYMWIMDAXl` |
| | Alarm | `FosRestaurant-OutboxFailureQueueAlarmB4029836-FtB2eUEdloNV` |
| HTTP API | Api / Stage | `ov5aeaff1b` / `$default` |

Every function, role, policy and log group follows the same naming as Phase 2 with the `FosRestaurant-` prefix. Lambdas: Python 3.14, arm64, 256 MB, 30 s, handlers `fos_restaurant.api.handler` and `fos_restaurant.commands.handler`.

**Shared layer naming:** both stacks' layers are versions of one layer named `SharedLayer27DFABF0`, because CDK derives the name from the construct ID, not the stack. Payment uses version 1 and Restaurant version 2. Each stack still owns and deletes only its own version.

### DynamoDB table

Same settings as Payment (on-demand, `NEW_IMAGE` stream, TTL `expiresAt`, destroy on delete).

| PK | SK | Holds |
|---|---|---|
| `RESTAURANT#<restaurantId>` | `META` | name, `status` (`OPEN`/`CLOSED`), `createdAt` |
| `RESTAURANT#<restaurantId>` | `PRODUCT#<productId>` | name, `priceMinor`, `available`, `version` (optimistic locking) |
| `DECISION#<orderId>` | `META` | first `CONFIRMED`/`REJECTED` decision for an order, so a retry can't change the answer |
| `INBOX#<messageId>` | `META` | processed-command marker, 7-day TTL |
| `OUTBOX#<messageId>` | `META` | event waiting to be published, 7-day TTL |

### HTTP API

No authorizer, CORS, access logs or throttling overrides, and **no GET routes**: writes return the full item.

| Route | Does |
|---|---|
| `POST /restaurants` | `{"name", "status"?}` → 201 (status defaults to `OPEN`) |
| `PATCH /restaurants/{restaurantId}` | `{"name"?, "status"?}` (at least one) → 200 / 404 |
| `POST /restaurants/{restaurantId}/products` | `{"name", "priceMinor", "available"?}` → 201, version 1, **publishes `ProductChanged`** / 404 |
| `PATCH /restaurants/{restaurantId}/products/{productId}` | `{"name"?, "priceMinor"?, "available"?}` → 200, version + 1, **publishes `ProductChanged`** / 404 / 409 on a concurrent update |

### Messages

| Direction | Type | Via |
|---|---|---|
| in | `ConfirmOrder` | `RestaurantCommands` queue |
| out | `RestaurantConfirmed`, `RestaurantRejected` (`RESTAURANT_NOT_FOUND`, `RESTAURANT_CLOSED`, `PRODUCT_NOT_IN_RESTAURANT`, `PRODUCT_UNAVAILABLE`) | outbox → bus (`fos.restaurant`) → `OrderEvents` |
| out | `ProductChanged` (name, price, version) | from the API, same path |

### IAM

Same pattern as Payment: Api and Commands get read/write on this table, Commands gets receive/delete on `RestaurantCommands`, and Outbox gets stream read, `PutEvents` on the bus and send to its failure queue.

---

## Phase 4 – `FosOrder`

The saga orchestrator. It accepts orders, drives Payment and Restaurant with commands, and tracks each order to a final status. It is the only service that sends commands, and it reads every event on the bus through `OrderEvents`.

### Resources (26)

Like the other services, minus a commands consumer: one HTTP API with 2 routes, two "worker" Lambdas and an outbox.

| Group | Type | Logical ID | Physical name / ID |
|---|---|---|---|
| Data | `AWS::DynamoDB::Table` | `TableCD117FA1` | `FosOrder-TableCD117FA1-4XK552V409QW` |
| Layer | `AWS::Lambda::LayerVersion` | `SharedLayer27DFABF0` | `SharedLayer27DFABF0:3` |
| API λ | `AWS::Lambda::Function` | `ApiFunctionCE271BD4` | `FosOrder-ApiFunctionCE271BD4-dB2K1VyAy4rX` |
| | `AWS::IAM::Role` / `Policy` | `ApiFunctionServiceRole…` | `FosOrder-ApiFunctionServiceRole52B9747B-WiGMUPnYZerX` |
| | `AWS::Logs::LogGroup` | `ApiFunctionLogGroupB51C7C52` | `/aws/lambda/FosOrder-ApiFunctionCE271BD4-dB2K1VyAy4rX` |
| Orchestrator λ | `AWS::Lambda::Function` | `Orchestrator6EF1216F` | `FosOrder-Orchestrator6EF1216F-t86gVQfdv41R` |
| | `AWS::IAM::Role` / `Policy` | `OrchestratorServiceRole…` | `FosOrder-OrchestratorServiceRole19534BDD-ySj6x5hM9Km7` |
| | `AWS::Logs::LogGroup` | `OrchestratorLogGroup106C61D4` | `/aws/lambda/FosOrder-Orchestrator6EF1216F-t86gVQfdv41R` |
| | `AWS::Lambda::EventSourceMapping` | `OrchestratorSqsEventSource…` | `b76d2724-e128-4065-95ca-b05762f615ee` |
| Outbox λ | `AWS::Lambda::Function` | `OutboxFunctionAB2D95BD` | `FosOrder-OutboxFunctionAB2D95BD-NYUIkaO7Udql` |
| | `AWS::IAM::Role` / `Policy` | `OutboxFunctionServiceRole…` | `FosOrder-OutboxFunctionServiceRole3524289E-75nwSbl9RYnS` |
| | `AWS::Logs::LogGroup` | `OutboxFunctionLogGroupCD15F1B8` | `/aws/lambda/FosOrder-OutboxFunctionAB2D95BD-NYUIkaO7Udql` |
| | `AWS::Lambda::EventSourceMapping` | `OutboxFunctionDynamoDBEventSource…` | `b86606f9-8858-4db0-a761-e195d4483d3f` |
| | `AWS::SQS::Queue` | `OutboxFailureQueueF968CAFD` | `FosOrder-OutboxFailureQueueF968CAFD-r73NxJBEayKv` |
| | `AWS::CloudWatch::Alarm` | `OutboxFailureQueueAlarmB4029836` | `FosOrder-OutboxFailureQueueAlarmB4029836-mJa69Vi6yNHx` |
| HTTP API | `AWS::ApiGatewayV2::Api` / `Stage` | `ApiF70053CD` / `ApiDefaultStage…` | `jjdvbcmwsc` / `$default` |
| | `AWS::ApiGatewayV2::Integration` | `ApiPOSTordersApiIntegration…` | `a7n7o5p` |
| | `AWS::ApiGatewayV2::Route` × 2 | `ApiPOSTorders…`, `ApiGETordersorderId…` | `uos81t8`, `nogig86` |
| | `AWS::Lambda::Permission` × 2 | one per route | – |
| | `AWS::CDK::Metadata` | `CDKMetadata` | – |

### Lambda functions

All three: Python 3.14, arm64, 256 MB, 30 s timeout, layer `SharedLayer27DFABF0:3`.

| Function | Handler | Trigger | Environment |
|---|---|---|---|
| Api | `fos_order.api.handler` | HTTP API | `TABLE_NAME`, `MAX_ITEMS_PER_ORDER=10` |
| Orchestrator | `fos_order.events.handler` | `OrderEvents` queue: batch 10, `ReportBatchItemFailures` | `TABLE_NAME` |
| Outbox | ✅ `fos_shared.outbox_publisher.handler` | table stream (same settings as the other services) | `EVENT_BUS_NAME`, `PAYMENT_COMMANDS_QUEUE_URL`, `RESTAURANT_COMMANDS_QUEUE_URL` |

This is the only outbox that sends **commands**. Rows whose message type appears in `COMMAND_QUEUE_ENV` (`DebitPayment`, `RefundPayment`, `ConfirmOrder`) go straight to the matching queue, not to the bus.

### DynamoDB table

`FosOrder-TableCD117FA1-4XK552V409QW`: same settings as the other services (on-demand, `NEW_IMAGE` stream, TTL `expiresAt`, destroy on delete), plus one GSI.

| PK | SK | Holds |
|---|---|---|
| `ORDER#<orderId>` | `META` | account, restaurant, priced `items`, `totalMinor`, `status`, `version`, timestamps, and `sweepPk`/`sweepSk` while in progress |
| `ORDER#<orderId>` | `STEP#<000001>` | status history: one row per transition (`status`, `detail`, `messageId`, `at`) |
| `CATALOG#<restaurantId>` | `PRODUCT#<productId>` | Order's copy of the menu (`name`, `priceMinor`, `version`), kept current from `ProductChanged` |
| `INBOX#<messageId>` | `META` | processed-event marker, 7-day TTL |
| `OUTBOX#<messageId>` | `META` | command waiting to be sent, 7-day TTL |

**GSI `ActiveOrders`** (`ACTIVE`): `sweepPk` / `sweepSk`, projection `ALL`. The index is sparse: only non-final orders carry the keys, and they're removed when an order reaches `CONFIRMED`, `FAILED` or `CANCELLED`. It's meant for a sweeper that finds stuck orders, but no sweeper Lambda is deployed yet.

### The saga

Steps run one after another, each committed in one transaction (order update + step row + inbox marker + next command):

```
POST /orders ──▶ PAYMENT_PENDING ──DebitPayment──▶ Payment
                   │ PaymentFailed ─────────────────────────────────▶ FAILED
                   │ PaymentCompleted
                   ▼
               RESTAURANT_PENDING ──ConfirmOrder──▶ Restaurant
                   │ RestaurantConfirmed ───────────────────────────▶ CONFIRMED
                   │ RestaurantRejected
                   ▼
               REFUND_PENDING ──RefundPayment──▶ Payment
                   │ PaymentRefunded ───────────────────────────────▶ CANCELLED
```

An event that doesn't fit the order's current status is logged and ignored. That's how duplicates and late events are absorbed. Concurrent events for one order are serialized by the `version` condition: the loser fails and SQS redelivers it.

### HTTP API

No authorizer, CORS, access logs or throttling overrides.

| Route | Does |
|---|---|
| `POST /orders` | `{"accountId", "restaurantId", "items": [{"productId", "quantity"}]}` → **202** `{trackingId, status: PAYMENT_PENDING, totalMinor}`. Prices come from the local catalog; 422 if a product isn't on that restaurant's catalog or total quantity > 10; 400 on a malformed body |
| `GET /orders/{orderId}` | 200 `{trackingId, status, restaurantId, items, totalMinor, createdAt, updatedAt, steps[]}` / 404 |

### Messages

| Direction | Type | Via |
|---|---|---|
| out (commands) | `DebitPayment`, `RefundPayment` | outbox → `PaymentCommands` |
| out (commands) | `ConfirmOrder` | outbox → `RestaurantCommands` |
| in | `PaymentCompleted`, `PaymentFailed`, `PaymentRefunded`, `RestaurantConfirmed`, `RestaurantRejected`, `ProductChanged` | `OrderEvents` |

### IAM

Api and Orchestrator: read/write on this table (including the GSI). Orchestrator: receive/delete on `OrderEvents`. Outbox: stream read, `events:PutEvents` on the bus, `sqs:SendMessage` on both command queues, and send to its failure queue.

---

## Idle cost (all deployed stacks)

About **$0.60/month**: six standard alarms at $0.10 each (3 DLQ alarms and 3 outbox failure alarms). Everything else is pay-per-use and free when idle: on-demand DynamoDB (including the GSI), Lambda, the three HTTP APIs, SQS, the custom event bus, and Lambda reading DynamoDB streams. Log groups cost only for what's written. A full order costs a few dozen requests in total, well under a cent.

## Known issues

1. **Outbox handler typo in FosPayment and FosRestaurant (blocks the saga).** Their outbox Lambdas run `fos_share.outbox_publisher.handler`. `FosOrder`'s outbox is already correct. The effect:
   - `POST /orders` returns 422 for every product, because Order's catalog is empty: no `ProductChanged` has ever been published.
   - Any order that does get created stops at `PAYMENT_PENDING`. Payment debits the account, but `PaymentCompleted` fails to publish.

   Fixed in `infra/infra/components.py:176`. Deploy with:
   ```powershell
   cdk deploy FosPayment FosRestaurant
   ```
   Products created before the redeploy never reached Order's catalog. Create new ones, or update existing ones (any PATCH publishes a fresh `ProductChanged`).
2. **Order's catalog only knows products changed since Order was deployed.** It's built from events, not backfilled. This is expected for this design, but worth knowing when testing.
3. **No sweeper.** The `ActiveOrders` index tracks in-progress orders, but nothing reads it yet, so an order stuck by a lost message stays stuck.
4. **Payment unit tests are stale.** `services/payment/tests` targets an earlier API (`store.py`, `ownerName`) and fails at collection.
5. **APIs are public.** No auth, no throttling and no CORS. Fine for learning, but anyone with the URL can create accounts, credit them and place orders.
6. **Carried over from Phase 1:** alarms have no actions, the bus→`OrderEvents` rule target has no DLQ, and the bus has no archive.

## Verify it yourself

```powershell
$R = "us-east-2"
aws cloudformation list-stack-resources --stack-name FosPayment --region $R --output table
# Should print fos_shared.outbox_publisher.handler for all three
"FosPayment-OutboxFunctionAB2D95BD-biKGrE6M20sJ", "FosRestaurant-OutboxFunctionAB2D95BD-9hmTojWMS2t1", "FosOrder-OutboxFunctionAB2D95BD-NYUIkaO7Udql" |
  ForEach-Object { aws lambda get-function-configuration --function-name $_ --region $R --query Handler }

# Orders still in progress (the sparse ActiveOrders index)
aws dynamodb query --table-name FosOrder-TableCD117FA1-4XK552V409QW --index-name ActiveOrders --region $R `
  --key-condition-expression "sweepPk = :p" --expression-attribute-values '{\":p\":{\"S\":\"ACTIVE\"}}'

# Tail a function's logs
aws logs tail /aws/lambda/FosPayment-Commands361DBF70-PNkquJkpEyh0 --region $R --since 10m --follow
```

**End-to-end test** (after the redeploy): run the **Order (end-to-end saga)** folder of the Postman collection with the Collection Runner. It creates an account, restaurant and product, then runs three orders:

| Scenario | Expected steps | Balance after |
|---|---|---|
| 2 × 1450 | `PAYMENT_PENDING → RESTAURANT_PENDING → CONFIRMED` | 5000 → 2100 |
| product unavailable | `PAYMENT_PENDING → RESTAURANT_PENDING → REFUND_PENDING → CANCELLED` | 2100 (debited, then refunded) |
| 10 × 1450 | `PAYMENT_PENDING → FAILED` (`INSUFFICIENT_FUNDS`) | 2100 |

If an order hangs, find where it stopped from its correlation ID (the order ID) across the logs:

```powershell
$order = "<trackingId>"
foreach ($fn in "FosOrder-Orchestrator6EF1216F-t86gVQfdv41R", "FosOrder-OutboxFunctionAB2D95BD-NYUIkaO7Udql",
                "FosPayment-Commands361DBF70-PNkquJkpEyh0", "FosPayment-OutboxFunctionAB2D95BD-biKGrE6M20sJ",
                "FosRestaurant-Commands361DBF70-ggqajG1PkfDo", "FosRestaurant-OutboxFunctionAB2D95BD-9hmTojWMS2t1") {
  "== $fn"; aws logs tail /aws/lambda/$fn --region $R --since 30m --filter-pattern "`"$order`""
}
```
