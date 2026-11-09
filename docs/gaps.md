# FOS – Known Gaps

What is missing or unfinished, in the order worth tackling it. For what is live in AWS, see [deployed-resources.md](deployed-resources.md).

| | |
|---|---|
| Last checked | 2026-11-09 |
| Tests | 21 pass, 2 suites fail to load |
| Live stacks | `FosMessaging` only |

The **Owner** column says who should make each change. Items marked *you* touch `services/`, which you write yourself. Items marked *either* are infra, tests or docs, which I can do when asked.

## Summary

| # | Gap | Area | Severity | Owner |
|---|---|---|---|---|
| 1 | Payment, Restaurant and Order stacks are deleted | Deploy | 🔴 Blocking | either |
| 2 | The saga has never run end to end in AWS | Deploy | 🔴 Blocking | either |
| 3 | Plain `pytest` skips restaurant and order | Tests | 🟠 High | either |
| 4 | Payment tests are stale and fail to load | Tests | 🟠 High | either |
| 5 | Infra test is a `cdk init` leftover and fails to load | Tests | 🟠 High | either |
| 6 | No tests for the DynamoDB handlers | Tests | 🟠 High | either |
| 7 | No sweeper for stuck orders | Design | 🟡 Medium | you |
| 8 | Alarms notify no one | Ops | 🟡 Medium | either |
| 9 | The bus→`OrderEvents` rule has no DLQ, and the bus has no archive | Ops | 🟡 Medium | either |
| 10 | Order's catalog is not backfilled | Design | 🟡 Medium | you |
| 11 | APIs are public: no auth, throttling or CORS | Security | 🟡 Medium | either |
| 12 | Node v26 is not supported by jsii | Tooling | ⚪ Low | you |

---

## Deploy

### 1. Payment, Restaurant and Order stacks are deleted

CloudFormation shows:

| Stack | Status | When (UTC) |
|---|---|---|
| `FosMessaging` | `UPDATE_COMPLETE` | 2026-11-08 17:28 |
| `FosPayment` | `DELETE_COMPLETE` | 2026-11-08 21:09 |
| `FosRestaurant` | `DELETE_COMPLETE` | 2026-11-08 21:10 |
| `FosOrder` | `DELETE_COMPLETE` | 2026-11-08 21:11 |
| `FosOrder` | `REVIEW_IN_PROGRESS`, change set `cdk-deploy-change-set` created but not executed | 2026-11-09 12:33 |

The `REVIEW_IN_PROGRESS` stack is an empty shell. A `cdk deploy` created its change set and then stopped, either at the approval prompt or because it was interrupted. The next `cdk deploy` reuses it.

**Fix:**
```powershell
cd infra
cdk deploy FosPayment FosRestaurant FosOrder
```
If `FosOrder` refuses to deploy because of the stuck change set, delete the empty stack first. It owns no resources:
```powershell
aws cloudformation delete-stack --stack-name FosOrder --region us-east-2
```

**After the redeploy:**
- API URLs, table names and function names will all be new. Update the `paymentUrl`, `restaurantUrl` and `orderUrl` variables in the [Postman collection](postman/fos.postman_collection.json), and refresh [deployed-resources.md](deployed-resources.md).
- All data is gone: accounts, restaurants, products and orders. The tables had `RemovalPolicy.DESTROY`.
- The outbox handler typo (`fos_share` → `fos_shared`) is fixed, because the stacks are rebuilt from the corrected `infra/infra/components.py`.

**Done when:** all four stacks are `CREATE_COMPLETE` or `UPDATE_COMPLETE`, and every outbox Lambda's handler is `fos_shared.outbox_publisher.handler`.

### 2. The saga has never run end to end in AWS

While all four stacks were up, the outbox typo stopped every order at `PAYMENT_PENDING`. So these flows have never been exercised against real AWS:
- `PaymentCompleted`
- `ConfirmOrder`
- `OrderConfirmed`
- the refund path

**Fix:** after #1, run the **Order (end-to-end saga)** folder of the Postman collection with the Collection Runner. It runs these three scenarios:

| Scenario | Expected final status |
|---|---|
| 2 × 1450 with balance 5000 | `CONFIRMED`, balance 2100 |
| product unavailable | `CANCELLED`, balance unchanged after the refund |
| 10 × 1450 with balance 2100 | `FAILED` (`INSUFFICIENT_FUNDS`) |

**Done when:** all three scenarios reach their expected status, and every DLQ and failure queue is empty.

---

## Tests

Current results:

| Suite | Result |
|---|---|
| `infra/shared/tests` (outbox publisher) | ✅ 3 passed |
| `services/restaurant/tests` (`decide`) | ✅ 5 passed |
| `services/order/tests` (saga `transition`) | ✅ 13 passed |
| `services/payment/tests` | ❌ `ImportError: cannot import name 'store' from 'fos_payment'` |
| `infra/tests` | ❌ `ModuleNotFoundError: No module named 'infra.infra_stack'` |

### 3. Plain `pytest` skips restaurant and order

[pytest.ini](../pytest.ini) lists only `infra/shared` and `services/payment` in `pythonpath` and `testpaths`. A plain `pytest` therefore runs 3 working tests plus one broken suite, and never runs the 18 restaurant and order tests.

**Fix:** add `services/restaurant` and `services/order` to both settings. Each service uses its own package name (`fos_payment`, `fos_restaurant`, `fos_order`), so the services can share one `pythonpath`. The only possible conflict is between test modules: two `conftest.py` files or test files with the same name. Give each test file a unique name, or add an `__init__.py` to each `tests/` folder.

**Done when:** `pytest` at the repo root runs every suite.

### 4. Payment tests are stale

`services/payment/tests` was written for the first version of the payment service, which had `store.py`, `ownerName` and `openingBalanceMinor`. Your rewrite (`api.py`, `commands.py`, `keys.py`) has a different API, so the suite fails to load and there are no payment tests at all.

This matters most here because payment is the code that moves money. Cases worth covering, using moto, which is already in `requirements-dev.txt`:
- `DebitPayment` with enough funds: balance drops, a `LEDGER#` row is written, and `PaymentCompleted` lands in the outbox.
- `DebitPayment` without enough funds: balance unchanged, and `PaymentFailed` (`INSUFFICIENT_FUNDS`) lands in the outbox.
- The same command delivered twice: debited once, and the inbox catches the duplicate.
- `RefundPayment` after a debit: balance restored, refunded exactly once.
- `RefundPayment` for an order that was never debited: no money is created.
- Credit with the same `CREDITREF#` twice: credited once.

**Done when:** the suite loads and covers the cases above.

### 5. Infra test is a `cdk init` leftover

`infra/tests/unit/test_infra_stack.py` imports `infra.infra_stack.InfraStack`, which has never existed. Meanwhile the real stacks have no tests. A synth test would have caught the `fos_share` typo before it was deployed.

**Fix:** replace it with `aws_cdk.assertions.Template` tests that synthesize all four stacks through `build_stacks` and check:
- every Lambda's `Handler` points at a module that exists: `fos_shared.outbox_publisher.handler`, `fos_payment.api.handler`, and so on
- each outbox Lambda's stream filter matches `OUTBOX#` INSERTs, and the stream has `ReportBatchItemFailures`
- `COMMAND_QUEUE_ENV` maps every command to the right queue
- the `EventsToOrder` rule matches `fos.payment` and `fos.restaurant`
- the `ActiveOrders` GSI exists on the Order table

**Done when:** `pytest infra/tests` passes, and mistyping a handler makes it fail.

### 6. No tests for the DynamoDB handlers

The restaurant and order tests cover only the pure functions (`decide`, `transition`). These modules, which write to DynamoDB, have no tests:

| Module | What is untested |
|---|---|
| `fos_restaurant/api.py` | create restaurant and product, PATCH publishes `ProductChanged` |
| `fos_restaurant/commands.py` | `DECISION#` row and decision event written in one transaction, duplicate command |
| `fos_order/api.py` | 422 for unknown products and more than `MAX_ITEMS_PER_ORDER` items, the order + `STEP#` + `DebitPayment` transaction, 404 |
| `fos_order/events.py` | version conflict, duplicate event, `ProductChanged` updating the catalog |

These are where the transactions, duplicate checks and optimistic locking live, so a bug there loses or duplicates a step in the saga.

**Done when:** each module has moto-backed tests for the happy path, the duplicate path and, for Order, the version conflict.

---

## Design and operations

### 7. No sweeper for stuck orders

Every in-progress order is written to the sparse `ActiveOrders` GSI (`sweepPk = ACTIVE`, `sweepSk = createdAt`), but nothing reads it. An order whose message is lost, or ends up in a DLQ, stays at `PAYMENT_PENDING` or `RESTAURANT_PENDING` forever.

**Fix:** a scheduled Lambda (an EventBridge Scheduler rule, every few minutes) that queries `ActiveOrders` for orders older than a timeout. For each one it either re-sends the current step's command, or moves the order to `FAILED` or `REFUND_PENDING`. It writes through the same versioned update as the orchestrator, so a late event and the sweeper can't both win.

### 8. Alarms notify no one

The six alarms (3 DLQ, 3 outbox failure queue) go into `ALARM` but have no actions.

**Fix:** an SNS topic with an email subscription, and `alarm.add_alarm_action(SnsAction(topic))` in `components.dlq_alarm`. The project name mentions SNS, and this is a natural first use of it.

### 9. The bus→`OrderEvents` rule has no DLQ, and the bus has no archive

If EventBridge can't deliver to `OrderEvents`, it retries for 24 hours and then drops the event without telling anyone. With no archive, the event can't be replayed either.

**Fix:**
- In `MessagingStack`, give the rule target `dead_letter_queue=...` and `retry_attempts`.
- Add an `Archive` on the bus with a retention period, so events can be replayed after a bug fix.

### 10. Order's catalog is not backfilled

Order builds its product catalog only from `ProductChanged` events. A product created before Order was deployed, or before the outbox fix, is unknown to Order, so `POST /orders` returns 422 for it. After the redeploy in #1 everything starts empty, so this won't bite until a stack is replaced on its own.

**Options:**
- republish `ProductChanged` for every product from a one-off script or an admin endpoint
- use the bus archive (#9) to replay events

### 11. APIs are public

All three HTTP APIs have no authorizer, no throttling and no CORS. Anyone with a URL can create accounts, credit them with money, and place orders. This is fine for a learning stack that is torn down often.

**Fix, when it matters:**
- stage throttling (`default_route_options` / `throttle` on the stage)
- an IAM or JWT authorizer, with at least the credit endpoint locked down
- a CORS config, if a browser front end is added

### 12. Node v26 is not supported by jsii

CDK prints the "untested Node.js version" warning on every run, and the infra test run ended with a jsii `ENOTEMPTY` temp-folder error on Windows. It's harmless today, but it could explain a strange CDK failure later.

**Fix:** use Node 22 or 24 LTS for the CDK CLI, e.g. with `nvm use 22`.
