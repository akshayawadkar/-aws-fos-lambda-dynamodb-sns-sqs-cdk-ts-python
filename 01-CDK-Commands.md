# CDK Commands

Run these from the `infra` directory.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## List and Synthesize

```powershell
cdk ls
cdk synth
cdk synth FosMessaging
cdk synth FosPayment
cdk synth FosRestaurant
cdk synth FosOrder
```

## Diff by Stack

```powershell
cdk diff FosMessaging
cdk diff FosPayment
cdk diff FosRestaurant
cdk diff FosOrder
```

## Deploy by Stack

```powershell
cdk deploy FosMessaging
cdk deploy FosPayment
cdk deploy FosRestaurant
cdk deploy FosOrder
```

## Deploy All

```powershell
cdk deploy --all
```

## Destroy by Stack

```powershell
cdk destroy FosOrder
cdk destroy FosRestaurant
cdk destroy FosPayment
cdk destroy FosMessaging
```
