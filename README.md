# Pills By Post — IntakeQ integration platform

Serverless (AWS Lambda + DynamoDB + API Gateway) integration for Pills By Post:
IntakeQ intake → eligibility → Converge Pay payment link; provider approval →
order; fulfillment by shipping-vendor CSV export, Honeybee Health Nectar
(Partners API), or the patient's local pharmacy; React Admin portal.

## Layout

```
pillsbypost.yaml      core stack: tables, queues, webhook API + WAF, Lambdas, SES, alarms
webui.yaml            admin stack: Cognito, S3/CloudFront admin site, admin API, config tables, deploy role
github-oidc.yaml      management-account stack: GitHub OIDC provider + GitHubActionsRole
lambdas/<name>/       one directory per function (lambda_function.py + requirements.txt)
layers/common/        shared package `common/` (bundled into every Lambda zip by scripts/build-lambda.sh)
frontend-app/         React Admin (Vite)
tests/                pytest + moto
.github/workflows/    test.yml, dev-/prod-deploy-lambda.yml, dev-/prod-deploy-web.yml
```

## Deploy order (per environment)

1. `webui-<env>` (webui.yaml) — owns the API Gateway account setting and config tables.
2. `pillsbypost-<env>` (pillsbypost.yaml) — needs `OpsAlertEmail` and `WebhookPathToken` (`openssl rand -hex 16`).
3. Set the Secrets Manager placeholders (`pillsbypost/<env>/*`), verify SES identities, fill the Settings table.
4. GitHub Actions: *Deploy Lambda to <ENV>* (`all`), then *Deploy Web Interface to <ENV>*.

Use CloudFormation change sets, never direct updates. Prod workflows only run from a `v*` tag.

## Local development

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/
scripts/build-lambda.sh intake-webhook   # builds lambdas/intake-webhook/deployment_package.zip
```

Lambdas log at `LOG_LEVEL` (`DEBUG` in dev, `INFO` in prod); PHI is DEBUG-only.
