import os

from aws_cdk import App, Environment

from infra.app import build_stacks

app = App()
build_stacks(app, Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-east-2"))
app.synth()
