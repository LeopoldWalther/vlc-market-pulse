# VLC Real Estate Analytics — Data Collection Layer (Bronze)

## Overview

The Bronze Collector is a scheduled AWS Lambda function that fetches Valencia real estate listings from the Idealista API and stores raw paginated JSON responses in the S3 bronze layer. It is the entry point of the medallion architecture.

## Architecture

```
┌──────────────────┐
│   EventBridge    │  cron(0 12 ? * SUN *)  — every Sunday 12:00 UTC
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌───────────────────────────────┐
│  Bronze Collector│────▶│  AWS Secrets Manager           │
│  Python 3.12     │     │  lvw-api-credentials           │
│  256 MB / 900 s  │     │  pmv-api-credentials           │
└────────┬─────────┘     └───────────────────────────────┘
         │  OAuth2 token → paginated JSON pages
         │  PutObject: bronze/idealista/{op}_{YYYYMMDD}_{HHMMSS}_{page}.json
         ▼
┌──────────────────┐
│  S3 Bronze Layer │   (no email from the collector — the Step Functions
│  bronze/idealista│    orchestrator sends one summary per run & env)
└──────────────────┘
```

## S3 Key Format

```
bronze/idealista/{operation}_{YYYYMMDD}_{HHMMSS}_{page}.json
```

Examples:
```
bronze/idealista/sale_20260601_120044_1.json
bronze/idealista/sale_20260601_120044_2.json
bronze/idealista/rent_20260601_120044_1.json
```

Each file is one raw Idealista API response page (up to 50 listings).

## Source Code

**Files**:
[src/etl/data_collection/idealista_listings_collector.py](../src/etl/data_collection/idealista_listings_collector.py) (thin Lambda handler),
[src/etl/data_collection/bronze_collector.py](../src/etl/data_collection/bronze_collector.py) (collection logic).

### Key Functions / Classes

| Function / Class | Purpose |
|---|---|
| `lambda_handler(event, context)` | Entry point; reads `test_mode` from event payload, delegates to `BronzeCollector` |
| `build_collector(env)` | Factory — wires the production `BronzeCollector` from environment variables |
| `BronzeCollector.collect(test_mode)` | Fetch → parse → persist for both operations; returns a `CollectionResult` |
| `SearchConfig.sale()` / `SearchConfig.rent()` | Strategy variants encapsulating each operation's search URL |
| `IdealistaApiClient` | Adapter wrapping `requests` (OAuth2 + paginated fetch) behind the `SearchApiClient` protocol |

### Design

`idealista_listings_collector.py` is a **thin handler**: it only builds a `BronzeCollector` (Factory
— `build_collector`) and maps its `CollectionResult` onto the Lambda response. All collection
behaviour lives in `bronze_collector.py`:

- **Strategy** — `SearchConfig.sale()` / `SearchConfig.rent()` encapsulate the per-operation search
  URL; new operations are added as a new classmethod, not a branch.
- **Adapter** — `IdealistaApiClient` wraps `requests` behind the `SearchApiClient` protocol so the
  collector never speaks the vendor SDK directly.
- **Dependency Injection** — `BronzeCollector` takes `ObjectStore`, `SecretsProvider`, `MetricsPublisher` and
  `SearchApiClient` via its constructor; production wiring happens once, in `build_collector`, tests
  inject the `common` in-memory fakes instead.
- **Template Method** — `collect()` defines the fetch → parse → persist skeleton shared by both
  operations; `_collect_operation` fills in the per-operation steps.
- **Single Responsibility / Custom Exceptions** — `BronzeCollector` only orchestrates; storage,
  secrets and metrics are separate collaborators; API failures raise `IdealistaAPIError`.

### Search Parameters

- **Location**: Valencia city center (39.4693441, −0.379561), 1500 m radius
- **Property Type**: Homes, 100–160 m², elevator, good preservation
- **Operations**: `sale` (LVW credentials) and `rent` (PMV credentials)

### Test Mode

When invoked with `{"test_mode": true}` in the event payload:
- Limits collection to 1 page per operation (2 API calls total)

In dev, EventBridge automatically passes `{"test_mode": true}` via the target `input` field, so the scheduled run never exceeds 2 API calls per week.


## Terraform Module

**Path**: `infrastructure/modules/lambda_bronze/`

```
modules/lambda_bronze/
├── main.tf        # Lambda, IAM, EventBridge, CloudWatch log group
├── variables.tf   # Input variables (including test_mode)
└── outputs.tf     # function_name, function_arn, log_group_name, etc.
```

### Key Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `environment` | string | — | `dev` or `prod` |
| `aws_region` | string | — | AWS region |
| `s3_bucket_name` | string | — | Target S3 bucket |
| `test_mode` | bool | `false` | Pass `{"test_mode":true}` as EventBridge input |

### Resources Created

| Resource | Name pattern |
|---|---|
| Lambda function | `{env}-idealista-collector` |
| IAM role | `{env}-idealista-collector-lambda-role` |
| Lambda layer | `{env}-requests-layer` |
| EventBridge rule | `{env}-idealista-collector-weekly` |
| CloudWatch log group | `/aws/lambda/{env}-idealista-collector` |

### IAM Permissions

- **CloudWatch Logs**: `CreateLogGroup`, `CreateLogStream`, `PutLogEvents`
- **S3**: `PutObject`, `PutObjectAcl`, `ListBucket` on the listings bucket
- **Secrets Manager**: `GetSecretValue` on LVW + PMV secret ARNs

## Deployment

```bash
cd infrastructure/environments/dev   # or prod

# First time
terraform init

# Review
terraform plan -var-file="secrets.tfvars"

# Apply
terraform apply -var-file="secrets.tfvars"
```

**`secrets.tfvars`** (gitignored):
```hcl
idealista_api_key_lvw    = "..."
idealista_api_secret_lvw = "..."
idealista_api_key_pmv    = "..."
idealista_api_secret_pmv = "..."
notification_email       = "your@email.com"
```

## Testing

### Manual Invocation

```bash
# Full run (prod)
aws lambda invoke \
  --function-name prod-idealista-collector \
  --region eu-central-1 \
  response.json && cat response.json | jq .

# Limited run — 1 page each (same as scheduled dev run)
aws lambda invoke \
  --function-name dev-idealista-collector \
  --region eu-central-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"test_mode": true}' \
  response.json
```

### Unit & Integration Tests

```bash
cd src/etl
pytest data_collection/tests/ -v --cov=data_collection
```

### Verify S3 Output

```bash
aws s3 ls s3://dev-vlc-real-estate-analytics-listings/bronze/idealista/ \
  --recursive --region eu-central-1
```

## Monitoring

```bash
# Live logs
aws logs tail /aws/lambda/prod-idealista-collector --region eu-central-1 --follow

# Filter errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/prod-idealista-collector \
  --filter-pattern "ERROR" \
  --region eu-central-1
```

## Troubleshooting

| Problem | Solution |
|---|---|
| Lambda times out | Check API rate limits; reduce `max_items` in `SearchConfig` |
| Permission denied on S3 | Verify IAM policy includes the correct bucket ARN |
| Secrets not found | Check secret names in `variables.tf` match Secrets Manager |
| API 401 / token error | Credentials may have expired; rotate in Secrets Manager |
| EventBridge not triggering | Check rule is enabled: `aws events list-rules --region eu-central-1` |
| State lock | `terraform force-unlock <LOCK_ID>` |

## Security

- API credentials stored exclusively in AWS Secrets Manager
- `secrets.tfvars` excluded from version control via `.gitignore`
- S3 bucket has AES-256 server-side encryption
- IAM role follows least-privilege principle
- CloudWatch logs retained 30 days then auto-deleted

## Related Documentation

- [DATA_PROCESSING_LAYER.md](DATA_PROCESSING_LAYER.md) — Silver Cleaner (Bronze → Parquet)
- [../README.md](../README.md) — Project overview and full architecture
