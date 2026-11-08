from dataclasses import dataclass

from aws_cdk import App, Environment

from .messaging_stack import MessagingStack
from .order_stack import OrderStack
from .payment_stack import PaymentStack
from .restaurant_stack import RestaurantStack


@dataclass(frozen=True)
class Stacks:
    messaging: MessagingStack
    payment: PaymentStack | None = None
    restaurant: RestaurantStack | None = None
    # order: OrderStack | None = None


def build_stacks(app: App, env: Environment) -> Stacks:
    messaging = MessagingStack(app, "FosMessaging", env=env)
    return Stacks(
        messaging=messaging,
        payment=PaymentStack(app, "FosPayment", env=env, messaging=messaging),
        restaurant=RestaurantStack(app, "FosRestaurant", env=env, messaging=messaging),
        # order=OrderStack(app, "FosOrder", env=env, messaging=messaging),
    )
