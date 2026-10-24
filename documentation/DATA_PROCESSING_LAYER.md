# VLC Real Estate Analytics — Data Processing Layer (Silver)

## Overview

The Silver Cleaner is a scheduled AWS Lambda function that reads raw bronze JSON pages from S3, applies data quality rules to individual listings, and writes cleaned Parquet files back to S3 in a Hive-partitioned structure. It runs 30 minutes after the Bronze Collector every Sunday to ensure the latest snapshot is available.

## Architecture

```
┌──────────────────┐
│   EventBridge    │  cron(30 12 ? * SUN *)  — every Sunday 12:30 UTC
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Silver Cleaner  │  Lists bronze keys for latest snapshot_date,
│  Python 3.12     │  reads all pages, applies cleaning rules,
│  512 MB / 300 s  │  writes partitioned Parquet to silver layer
└────────┬─────────┘
         │
         ├── GetObject:  bronze/idealista/{op}_{YYYYMMDD}_{HHMMSS}_{page}.json
         │
         └── PutObject:  silver/idealista/operation={op}/snapshot_date=YYYY-MM-DD/part.parquet
```

## S3 Medallion Layout

| Layer | S3 Prefix | Format |
|---|---|---|
| Bronze (input) | `bronze/idealista/{op}_{YYYYMMDD}_{HHMMSS}_{page}.json` | Raw Idealista API JSON |
| Silver (output) | `silver/idealista/operation={op}/snapshot_date=YYYY-MM-DD/part.parquet` | Parquet, Hive-partitioned |

The silver prefix uses Hive-style partitioning (`operation=sale/`, `snapshot_date=2026-06-01/`) so pandas and Athena can read it efficiently without scanning all files.

## Data Quality Rules

The cleaner applies the following rules to every listing before writing to silver:

| Rule | Column | Drop condition |
|---|---|---|
| Missing price per m² | `priceByArea` | `None` or missing |
| Missing neighborhood | `neighborhood` | empty string or missing |
| Zero bathrooms | `bathrooms` | `<= 0` |
| Sale price outlier | `priceByArea` (sale only) | `< 1000` or `> 10000` |

Rent listings are **not** filtered on `priceByArea` range (rental prices differ significantly from sale prices).

### Columns in Silver Output

```
operation, province, municipality, district, neighborhood,
latitude, longitude, distance, address,
propertyCode, propertyType,
price, priceByArea, size, floor,
exterior, rooms, bathrooms, status,
newDevelopment, hasLift, parkingSpace,
snapshot_date
```

`snapshot_date` is derived from the bronze S3 key (the API payload does not include a collection timestamp).

## Source Code

### `silver_transform.py` — Pure Transform (no AWS)

**File**: [src/etl/data_processing/silver_transform.py](../src/etl/data_processing/silver_transform.py)

AWS-free module — can be unit-tested without mocking any services.

| Function | Purpose |
|---|---|
| `parse_key_metadata(key)` | Extracts `operation` and `snapshot_date` from a bronze S3 key |
| `clean(elements, snapshot_date, operation)` | Applies quality rules; returns list of cleaned row dicts |

### `silver_cleaner.py` — SilverCleaner (orchestration, no direct AWS)

**File**: [src/etl/data_processing/silver_cleaner.py](../src/etl/data_processing/silver_cleaner.py)

| Class / Method | Purpose |
|---|---|
| `SilverCleaner(object_store, bronze_prefix, silver_prefix)` | Owns the snapshot-selection + persistence rules |
| `SilverCleaner.clean_snapshots(target_date=None)` | List → read → clean → write; returns a `CleaningResult` |

### `silver_cleaning_lambda.py` — Thin Lambda Handler

**File**: [src/etl/data_processing/silver_cleaning_lambda.py](../src/etl/data_processing/silver_cleaning_lambda.py)

| Function | Purpose |
|---|---|
| `lambda_handler(event, context)` | Entry point; resolves the optional `snapshot_date` override, delegates to `SilverCleaner` |
| `build_cleaner(env)` | Factory — wires the production `SilverCleaner` from environment variables |

### Design

`SilverCleaner` **encapsulates** the cleaning rules (private `_list_snapshot_keys` /
`_read_elements` / `_write_parquet` helpers) and depends only on the `ObjectStore` protocol
(**Dependency Inversion**), so unit tests run against `InMemoryObjectStore` — no moto, no AWS. The
genuinely pure row-level rules stay in `silver_transform.clean`, called unchanged. The Lambda handler
is reduced to a **Factory** (`build_cleaner`) plus a thin call to `clean_snapshots()`; the incremental
`exists()` guard and the `rows_written` / `parquet_files_written` response contract are unchanged.

### Environment Variables

| Variable | Example | Description |
|---|---|---|
| `S3_BUCKET` | `dev-vlc-real-estate-analytics-listings` | Shared bucket for bronze + silver |
| `BRONZE_PREFIX` | `bronze/idealista` | Input prefix |
| `SILVER_PREFIX` | `silver/idealista` | Output prefix |
| `SNS_TOPIC_ARN` | `arn:aws:sns:eu-central-1:...` | Alert topic on error |

## Terraform Module

**Path**: `infrastructure/modules/lambda_silver/`

```
modules/lambda_silver/
├── main.tf        # Lambda, IAM, EventBridge, CloudWatch log group + alarm
├── variables.tf   # Input variables
└── outputs.tf     # function_name, function_arn, log_group_name, etc.
```

### Key Variables

| Variable | Type | Description |
|---|---|---|
| `environment` | string | `dev` or `prod` |
| `aws_region` | string | AWS region |
| `s3_bucket_name` | string | Shared S3 bucket name |
| `s3_bucket_arn` | string | Shared S3 bucket ARN |
| `sns_topic_arn` | string | SNS topic for error alerts |
| `pandas_layer_arn` | string | `AWSSDKPandas-Python312` managed layer ARN (region-specific) |

### Resources Created

| Resource | Name pattern |
|---|---|
| Lambda function | `{env}-silver-cleaner` |
| IAM role | `{env}-silver-cleaner-lambda-role` |
| EventBridge rule | `{env}-silver-cleaner-weekly` |
| CloudWatch log group | `/aws/lambda/{env}-silver-cleaner` |
| CloudWatch alarm | `{env}-silver-cleaner-errors` |

### IAM Permissions (Least Privilege)

| Action | Scope |
|---|---|
| `s3:ListBucket` | Bucket-level, prefixes `bronze/idealista/*` and `silver/*` |
| `s3:GetObject` | `bronze/idealista/*` only (read) |
| `s3:PutObject`, `s3:PutObjectAcl` | `silver/*` only (write) |
| `logs:*` | Log group for this function only |
| `sns:Publish` | The notifications topic |

### pandas / pyarrow

pandas and pyarrow are **not bundled** in the deployment ZIP. They are provided by the AWS-managed `AWSSDKPandas-Python312` Lambda layer. The ARN is region-specific and passed via `pandas_layer_arn`:

```
eu-central-1: arn:aws:lambda:eu-central-1:336392948345:layer:AWSSDKPandas-Python312:16
```

Latest ARNs: https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html

### Deployment (Dev)

The silver module is wired into `infrastructure/environments/dev/main.tf`:

```hcl
module "silver_cleaner" {
  source = "../../modules/lambda_silver"

  environment      = var.environment
  aws_region       = var.aws_region
  s3_bucket_name   = module.listings_bucket.listings_bucket_name
  s3_bucket_arn    = module.listings_bucket.listings_bucket_arn
  sns_topic_arn    = module.idealista_notifications.topic_arn
  pandas_layer_arn = var.pandas_layer_arn  # default: eu-central-1 ARN
}
```

> Prod wiring is deferred until after a dev soak period to validate output quality before affecting production data.

## Testing

### Unit & Integration Tests

```bash
cd src/etl
pytest data_processing/tests/ -v --cov=data_processing
```

Test suite (26 tests, ≥ 96% coverage):

| Class | What it tests |
|---|---|
| `TestParseKeyMetadata` | Key parsing for all operations and edge cases |
| `TestRealBronzeSchemaContract` | Schema of actual bronze JSON files |
| `TestClean` | All quality rules; individual listing output; no aggregation |
| `TestLambdaHandlerCombinesPagesWritesParquet` | End-to-end mock: list → read → clean → Parquet |
| `TestLambdaHandlerIdempotency` | Re-run overwrites same output key |
| `TestLambdaHandlerNoAggregation` | Silver stores individual rows, not aggregated |
| `TestLambdaHandlerNoLatestJson` | No `latest.json` side-effect |
| `TestLambdaHandlerMissingEnvVars` | Raises on missing `S3_BUCKET` / `SILVER_PREFIX` |
| `TestLambdaHandlerEdgeCases` | Empty bronze bucket; single page |

> **Important**: Tests use `moto` v5 (`mock_aws()`, not the removed `mock_s3`). The boto3 S3 client is created **inside** `lambda_handler()` (not at module level) so moto can intercept it correctly.

### Manual Invocation

```bash
# Trigger silver cleaner manually
aws lambda invoke \
  --function-name dev-silver-cleaner \
  --region eu-central-1 \
  response.json && cat response.json | jq .

# Verify Parquet output
aws s3 ls s3://dev-vlc-real-estate-analytics-listings/silver/idealista/ \
  --recursive --region eu-central-1
```

### Read Silver Data Locally

```python
import boto3, pandas as pd, io

s3 = boto3.client("s3", region_name="eu-central-1")
obj = s3.get_object(
    Bucket="dev-vlc-real-estate-analytics-listings",
    Key="silver/idealista/operation=sale/snapshot_date=2026-06-01/part.parquet"
)
df = pd.read_parquet(io.BytesIO(obj["Body"].read()))
print(df.shape, df.columns.tolist())
```

## Monitoring

| Signal | Resource | Action |
|---|---|---|
| Lambda error | CW Alarm `{env}-silver-cleaner-errors` | SNS email |
| No silver output | Manual S3 check | Re-invoke Lambda |

```bash
# Live logs
aws logs tail /aws/lambda/dev-silver-cleaner --region eu-central-1 --follow
```

## Design Decisions

### Why scheduled (not S3-triggered)?

An S3 event notification per bronze JSON file would create 10+ Lambda invocations per collection run, with no coordination between pages of the same snapshot. A scheduled trigger fires once, 30 minutes after the collector, when all pages are guaranteed to be present.

### Why only the latest snapshot?

Silver is a **current-state** layer. It reflects the most recently collected snapshot for each operation. Historical comparison is done in the gold layer (FEATURE-004) by reading all dated silver partitions.

### Why individual listings (not aggregated)?

Aggregation (median price, price-per-m² by neighborhood) belongs in the gold layer where the aggregation logic can evolve independently of the silver cleaning rules. Silver is the single source of truth for cleaned individual records.

---

## Pipeline Orchestration (Step Functions)

> **FEATURE-007** — introduced in `feature/step-functions-orchestration`.

The three stages (bronze → silver → gold) are now orchestrated by an AWS Step Functions **Standard** state machine. A single EventBridge Scheduler trigger fires the state machine; the per-Lambda EventBridge rules are disabled (`create_schedule = false`) to prevent double-invocation.

### Architecture

```
┌─────────────────────────┐
│  EventBridge Scheduler  │  cron(0 12 ? * SUN *)  — every Sunday 12:00 UTC
└────────────┬────────────┘
             │ StartExecution (test_mode: true/false)
             ▼
┌─────────────────────────────────────────────────────────────┐
│  Step Functions — {env}-medallion-pipeline (STANDARD)       │
│                                                             │
│  RunBronzeCollector ──(200)──▶ RunSilverCleaner             │
│       │                             │                       │
│   (Catch / !=200)              (Catch / !=200)              │
│       │                             │                       │
│       ▼                             ▼                       │
│  CheckBronzeStatus           CheckSilverStatus              │
│       │  200                        │  200                  │
│       └──────────────┐  ┌───────────┘                       │
│                      ▼  ▼                                   │
│               RunGoldAggregator                             │
│                      │                                      │
│               (Catch / !=200)                               │
│                      │                                      │
│               CheckGoldStatus                               │
│                 │         │                                  │
│              200 ▼         ▼ other                          │
│         PipelineSucceeded  NotifyFailure ──▶ PipelineFailed │
└─────────────────────────────────────────────────────────────┘
```

### Retry behaviour

Each Task state retries up to **2 times** on transient Lambda service errors (`Lambda.ServiceException`, `Lambda.AWSLambdaException`, `Lambda.SdkClientException`, `Lambda.TooManyRequestsException`) with an initial interval of 30 s and a back-off rate of 2.0 (30 s → 60 s).

### Failure handling

Two failure paths stop the pipeline before downstream stages run:

| Path | Condition | Route |
|---|---|---|
| Raised exception | Lambda throws (after retries exhausted) | `Catch → NotifyFailure` with `ResultPath: $.error` |
| Swallowed error | Lambda returns `statusCode != 200` | `Choice default → NotifyFailure` |

`NotifyFailure` publishes an SNS message with the Step Functions execution name, then transitions to the `PipelineFailed` (Fail) state. This ensures the bronze handler's swallowed-500 return stops silver and gold from running.

### Email notifications

Each environment sends **exactly one email per run** — `NotifySuccess` (with bronze/silver/gold metrics) or `NotifyFailure`. The individual Lambdas send no success emails of their own. Because dev and prod share one inbox, the subject leads with an environment icon and label, followed by a status icon:

| Environment | Success | Failure |
|---|---|---|
| prod | `🏠 PROD ✅ VLC Market Pulse - weekly pipeline succeeded` | `🏠 PROD ❌ VLC Market Pulse - weekly pipeline FAILED` |
| dev | `🧪 DEV ✅ VLC Market Pulse - weekly pipeline succeeded` | `🧪 DEV ❌ VLC Market Pulse - weekly pipeline FAILED` |

The icons are defined in `locals.environment_icons` in `modules/pipeline_orchestrator/main.tf`.

### Terraform module

**Path**: `infrastructure/modules/pipeline_orchestrator/`

```
modules/pipeline_orchestrator/
├── main.tf                  # State machine, IAM roles, Scheduler, CloudWatch log group
├── state_machine.asl.json   # ASL definition (ARNs injected via templatefile)
├── variables.tf             # Input variables
└── outputs.tf               # state_machine_arn, state_machine_name, log_group_name, etc.
```

#### Key variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `environment` | string | — | `dev` or `prod` |
| `aws_region` | string | — | AWS region |
| `bronze_function_arn` | string | — | ARN of the bronze collector Lambda |
| `silver_function_arn` | string | — | ARN of the silver cleaner Lambda |
| `gold_function_arn` | string | — | ARN of the gold aggregator Lambda |
| `sns_topic_arn` | string | — | SNS topic for the per-run summary email (success or failure) |
| `test_mode` | bool | `false` | Passed to bronze Lambda; limits to 1 page/operation in dev |

#### Resources created

| Resource | Name pattern |
|---|---|
| Step Functions state machine | `{env}-medallion-pipeline` |
| SFN execution IAM role | `{env}-medallion-pipeline-sfn-role` |
| Scheduler trigger IAM role | `{env}-medallion-pipeline-scheduler-role` |
| EventBridge Scheduler schedule | `{env}-medallion-pipeline-weekly` |
| CloudWatch log group | `/aws/vendedlogs/states/{env}-medallion-pipeline` |

#### Logging

ALL-level logging is enabled. Execution history (including state input/output) is written to `/aws/vendedlogs/states/{env}-medallion-pipeline` with 30-day retention.

### Dev wiring

The orchestrator is wired into `infrastructure/environments/dev/main.tf`:

```hcl
module "pipeline_orchestrator" {
  source = "../../modules/pipeline_orchestrator"

  environment         = var.environment
  aws_region          = var.aws_region
  bronze_function_arn = module.idealista_collector.function_arn
  silver_function_arn = module.silver_cleaner.function_arn
  gold_function_arn   = module.gold_aggregator.function_arn
  sns_topic_arn       = module.idealista_notifications.topic_arn
  test_mode           = true  # 1 page/operation in dev
}
```

All three lambda modules set `create_schedule = false` — the orchestrator is the single trigger.

> **Prod wiring** is coordinated with FEATURE-006 (prod promotion). The `pipeline_orchestrator` module is applied to prod alongside the same `create_schedule = false` change on the three Lambda modules.

### Re-running a failed execution

To manually re-run the pipeline after a failure:

1. Open the [Step Functions console](https://eu-central-1.console.aws.amazon.com/states/home?region=eu-central-1) → **State machines** → `{env}-medallion-pipeline`.
2. Find the failed execution and open it to identify the failing stage.
3. Check CloudWatch logs at `/aws/vendedlogs/states/{env}-medallion-pipeline` for details.
4. Fix the root cause (e.g. re-invoke the bronze Lambda if the API was temporarily unavailable).
5. Click **New execution** → paste the original input JSON (or use `{"test_mode": false}` for prod):

```bash
# Start a new execution from the CLI
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:eu-central-1:<ACCOUNT_ID>:stateMachine:prod-medallion-pipeline" \
  --input '{"test_mode": false}' \
  --region eu-central-1
```

The state machine is **idempotent**: re-running bronze overwrites the same S3 key prefix; re-running silver overwrites the same silver partition; re-running gold overwrites `gold/aggregations/latest.json`.

### Why pandas layer instead of bundled?

The `AWSSDKPandas-Python312` managed layer saves ~50 MB of deployment package size, is kept up-to-date by AWS, and avoids the complexity of compiling native extensions (pyarrow) for the Lambda runtime.

## Related Documentation

- [DATA_COLLECTION_LAYER.md](DATA_COLLECTION_LAYER.md) — Bronze Collector (Idealista API → JSON)
- [../README.md](../README.md) — Project overview and full architecture
