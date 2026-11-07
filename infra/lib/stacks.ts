import {Stack, StackProps} from "aws-cdk-lib";
import {Construct} from "constructs";

export class MessagingStack extends Stack {

    constructor(scope: Construct, id: string, props?: StackProps) {
        super(scope, id, props);
    }

}

export interface PaymentStackProps extends StackProps {
    messaging: MessagingStack;
}


export class PaymentStack extends Stack {
    constructor(scope: Construct, id: string, props?: PaymentStackProps) {
        super(scope, id, props);
    }

}



export interface RestaurantStackProps extends StackProps {
    messaging: MessagingStack;
}
export class RestaurantStack extends Stack {
    constructor(scope: Construct, id: string, props?: RestaurantStackProps) {
        super(scope, id, props);
    }

}


export interface OrderStackProps extends StackProps {
    messaging: MessagingStack;
}
export class OrderStack extends Stack {
    constructor(scope: Construct, id: string, props?: OrderStackProps) {
        super(scope, id, props);
    }
}