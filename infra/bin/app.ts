import {App} from "aws-cdk-lib";
import {buildStacks} from "../lib/app";

const app = new App();
buildStacks(app, {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-2',
});