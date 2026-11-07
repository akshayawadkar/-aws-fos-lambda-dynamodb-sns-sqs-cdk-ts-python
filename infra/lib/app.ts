import {App, Environment} from "aws-cdk-lib";
import {MessagingStack, OrderStack, PaymentStack, RestaurantStack} from "./stacks";

export function buildStacks(app: App, env: Environment) {
    const messaging = new MessagingStack(app, 'FosMessaging', {env});
    return {
        messaging,
        payment: new PaymentStack(app, 'FosPayment', {env, messaging}),
        restaurant: new RestaurantStack(app, 'FosRestaurant', {env, messaging}),
        order: new OrderStack(app, 'FosOrder', {env, messaging}),
    }
}