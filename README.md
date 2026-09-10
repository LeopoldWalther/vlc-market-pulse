# VLC Market Pulse

**Automated, serverless price-trend intelligence for the Valencia real-estate market — built to run itself for the cost of a coffee per month.**

> A production-shaped data platform that has quietly collected a clean weekly price history of the Valencia housing market **since 2023**, and turns it into a live, public trend dashboard. Part data-engineering reference implementation, part working market-research product.

---

## Case Study

### The Problem

The Valencia property market is opaque. Asking prices drift week to week, listings appear and vanish, and there is **no free, reliable source of truth for how price-per-m² is actually trending** in specific neighbourhoods over time. Anyone trying to answer *"is this district getting more expensive, and is now a good time to buy or rent?"* is left manually eyeballing portals — slow, inconsistent, and impossible to do retroactively once a listing is gone.

### The Solution

An end-to-end, fully automated ETL pipeline that:
- pulls sale **and** rental listings from the official Idealista API on a fixed weekly schedule,
- refines them through a **Bronze → Silver → Gold medallion architecture** (raw JSON → cleaned Parquet → analytics-ready aggregations),
- and publishes the result to a **live static dashboard** showing €/m² trends for Valencia's key districts over multiple years.

No servers to babysit, no manual steps — the whole thing wakes up every Sunday, updates itself, and goes back to sleep.

### The Business Impact

- **Days → zero.** What used to be manual, repeated data collection is now a hands-off weekly snapshot with a multi-year history no manual process could reconstruct.
- **Better timing decisions.** Long-run €/m² trends per district turn gut feeling into evidence for buy/rent/invest decisions.
- **~€3/month, near-zero maintenance.** A serverless-first design means the platform costs less than a single coffee to run and needs no ongoing operations.

---

## Sister Project — `vlc-price-estimator`

This repository is the **lightweight, historical-trends** half of a two-part portfolio:

| | **VLC Market Pulse** (this repo) | **VLC Price Estimator** (sibling) |
|---|---|---|
| Data source | Official Idealista **API** (quota-limited, curated) | **Web scraping** (full Comunidad Valenciana inventory) |
| Compute | Serverless (Lambda) — short, cheap jobs | Long-running jobs (ECS / Fargate) |
| Focus | Multi-year **price-trend time series** | **Price estimation & feature analysis** over a large dataset |
| Demonstrates | Cost-aware cloud architecture · data-engineering discipline | Scalable pipelines · data science / ML |

Together they show the same market from two angles: a **cheap, disciplined trend engine** and a **scalable intelligence engine**.

---

### Key Features

- **Automated Data Collection** — Weekly Bronze Collector Lambda, Sundays 12:00 UTC
- **Automated Data Cleaning** — Weekly Silver Cleaner Lambda, Sundays 12:30 UTC (30 min after collector)
- **Automated Aggregations** — Weekly Gold Aggregator Lambda, Sundays 12:45 UTC (writes dashboard-ready JSON)
- **Medallion Architecture** — Bronze (raw JSON) → Silver (cleaned Parquet) → Gold (aggregations JSON)
- **Real Estate Listings** — Sale and rental property data from Idealista API v3.5
- **Historical Time-Series** — Append-only S3 storage for long-term market trend analysis
- **Multi-Environment** — Separate `dev` and `prod` environments; dev runs in `test_mode` (1 page/op)
- **Secure Secrets** — API credentials managed via AWS Secrets Manager
- **Serverless & Cost-Efficient** — Estimated < $5/month across both environments

## What This Project Demonstrates

This repository doubles as a working reference for the engineering practices I bring to client
work. It is intentionally small in surface area but production-shaped end to end.

| Capability | How it shows up here |
|---|---|
| **Clean Code · OOP · SOLID · Design Patterns** | Domain logic is separated from AWS edges; collaborators are injected (Dependency Injection), third-party SDKs sit behind project-owned interfaces (Adapter), and interchangeable algorithms are encapsulated (Strategy). Standards are codified in [`copilot-instructions.md`](.github/copilot-instructions.md). |
| **AWS · Serverless · IaC** | Event-driven Lambdas scheduled by EventBridge, least-privilege IAM, encrypted S3, Secrets Manager, SNS alerting — all provisioned with reusable Terraform modules across isolated `dev`/`prod` environments. |
| **Data Engineering · Medallion Architecture** | A Bronze → Silver → Gold pipeline: raw JSON lands in Bronze, is cleaned into Hive-partitioned Parquet in Silver, and aggregated into an analytics-ready contract in Gold. |
| **Agentic AI Workflows** | Features are planned, reviewed, and built through a three-stage **Architect · Review · Implement** agent workflow with a CI consistency gate — see [`.github/agents/WORKFLOW.md`](.github/agents/WORKFLOW.md). |
| **Test-Driven Development · CI/CD** | Every change follows RED → GREEN → REFACTOR with >80% coverage, enforced by pre-commit hooks (black, ruff, mypy) and GitHub Actions running pytest and Terraform validation. |

## Technology Stack

### Application
- **Runtime**: Python 3.12
- **AWS Services**: Lambda, S3, Secrets Manager, EventBridge, CloudWatch, SNS
- **API Integration**: Idealista Property Search API v3.5 (OAuth2)
- **Data Processing**: pandas + pyarrow via AWS-managed Lambda layer
- **Data Analysis**: Jupyter Notebooks

### Infrastructure
- **IaC Tool**: Terraform v1.14.3
- **Cloud Provider**: AWS (eu-central-1)
- **Compute**: Lambda Functions (serverless)
- **Storage**: S3 (AES-256 encrypted at rest)
- **Secrets**: AWS Secrets Manager
- **Scheduling**: EventBridge (cron)
- **Alerting**: SNS topics (error notifications)
- **Monitoring**: CloudWatch Logs + Metric Alarms
- **State Management**: S3 with native S3 locking

## Architecture

### Architecture Decisions & Trade-offs

| Decision | Why | Trade-off accepted |
|---|---|---|
| **Serverless (Lambda) over always-on servers** | Weekly, bursty workload — paying for idle compute makes no sense. Scales to zero, ~€3/month total. | Cold starts and the 15-min execution ceiling; unsuitable for long crawls (that is the sibling project's ECS/Fargate job). |
| **Medallion architecture (Bronze → Silver → Gold)** | Clean separation of concerns: immutable raw history, reproducible cleaning, and a stable analytics contract. Any layer can be rebuilt from the one below. | More moving parts and S3 round-trips than a single "clean-on-read" script. |
| **S3 as the data store (no database)** | Append-only, cheap, durable, and a perfect fit for immutable historical snapshots + Parquet analytics. | No ad-hoc SQL/indexing; querying means reading files (fine at this data volume). |
| **Static S3/CloudFront dashboard (no backend API)** | The Gold layer is pre-aggregated JSON, so the frontend is just static files — nothing to run, nothing to attack, near-zero cost. | Data is as fresh as the last weekly run, not real-time (perfectly acceptable for trend analysis). |
| **Terraform IaC across isolated dev/prod** | Reproducible, reviewable infrastructure; no click-ops drift. | Higher upfront authoring effort than console setup. |
| **Official API over scraping (in this repo)** | Reliable, ToS-compliant, low-maintenance — ideal for a disciplined long-run time series. | Hard monthly listing quota → small, curated dataset (the scraping sibling lifts this ceiling). |

### Data Flow

```
┌──────────────────┐
│   EventBridge    │  cron(0 12 ? * SUN *)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌─────────────────────────┐
│  Bronze Collector│────▶│  AWS Secrets Manager     │
│  Python 3.12     │     │  (LVW + PMV API creds)   │
│  256 MB / 900 s  │     └─────────────────────────┘
└────────┬─────────┘
         │  PutObject  bronze/idealista/{op}_{date}_{time}_{page}.json
         ▼
┌──────────────────┐     ┌─────────────────────────┐
│  S3 Bronze Layer │     │  SNS Topic               │
│  bronze/idealista│     │  (error alerts)          │
└────────┬─────────┘     └─────────────────────────┘
         │
         │  (30 min later)
         ▼
┌──────────────────┐
│   EventBridge    │  cron(30 12 ? * SUN *)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Silver Cleaner  │  Reads all bronze pages for latest snapshot,
│  Python 3.12     │  drops nulls / invalid prices / zero bathrooms,
│  512 MB / 300 s  │  injects snapshot_date, writes partitioned Parquet
└────────┬─────────┘
         │  PutObject  silver/idealista/operation={op}/snapshot_date=YYYY-MM-DD/part.parquet
         ▼
┌──────────────────┐
│  S3 Silver Layer │
│  silver/idealista│
└────────┬─────────┘
         │
         │  (15 min later)
         ▼
┌──────────────────┐
│   EventBridge    │  cron(45 12 ? * SUN *)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Gold Aggregator │  Reads full silver history, scopes to 3 districts,
│  Python 3.12     │  computes two-population aggregations (general +
│  512 MB / 300 s  │  relevant), writes frozen schema v1.0 JSON
└────────┬─────────┘
         │  PutObject  gold/aggregations/latest.json
         ▼
┌──────────────────┐
│  S3 Gold Layer   │
│  gold/aggregations│
└────────┬─────────┘
         │
         ├────────────────────────────────────────┐
         ▼                                        ▼
┌──────────────────┐                 ┌──────────────────────┐
│  Jupyter         │                 │  CloudFront + S3     │  vlc-report-dev / vlc-report
│  Notebooks       │                 │  Static frontend     │  Plain HTML + ESM + Plotly.js
│  pandas → silver │                 │  /gold/aggregations/ │  Two-population toggle
└──────────────────┘                 └──────────────────────┘
```

### S3 Medallion Layout

| Layer | S3 Prefix | Format | Written by |
|---|---|---|---|
| Bronze | `bronze/idealista/{op}_{YYYYMMDD}_{HHMMSS}_{page}.json` | Raw JSON (Idealista API response) | Bronze Collector |
| Silver | `silver/idealista/operation={op}/snapshot_date=YYYY-MM-DD/part.parquet` | Parquet (Hive-partitioned) | Silver Cleaner |
| Gold | `gold/aggregations/latest.json` | JSON (schema v1.0, full time-series aggregations) | Gold Aggregator |

### Silver Cleaning Rules

| Rule | Detail |
|---|---|
| Drop null `priceByArea` | Missing price per m² — unusable for analysis |
| Drop blank/missing `neighborhood` | Cannot attribute to a district |
| Drop `bathrooms <= 0` | Data quality issue |
| Sale filter: `1000 ≤ priceByArea ≤ 10000` | Removes outliers outside Valencia market range |
| Inject `snapshot_date` | Derived from bronze S3 key (no `dateDownload` in payload) |
| Keep individual listings | Silver stores one row per listing, not aggregated |

### Infrastructure Layout

```
infrastructure/
├── bootstrap/              # Remote state S3 bucket + DynamoDB lock (one-time)
├── shared/dns/             # ACM wildcard cert + Route 53 zone (shared across envs)
├── modules/
│   ├── lambda_bronze/      # Bronze Collector: Lambda, IAM, EventBridge, CloudWatch
│   ├── lambda_silver/      # Silver Cleaner: Lambda, IAM, EventBridge, CW Alarm
│   ├── lambda_gold/        # Gold Aggregator: Lambda, IAM, EventBridge, CW Alarm
│   ├── frontend/           # CloudFront + private S3 assets + OAC; Route 53 aliases
│   ├── s3/                 # S3 listings bucket (AES-256 encryption)
│   ├── secrets/            # Secrets Manager secrets for API credentials
│   └── sns/                # SNS topic for error alerting
└── environments/
    ├── dev/                # Dev environment (test_mode=true; vlc-report-dev.leopoldwalther.com)
    └── prod/               # Production environment
```

### Source Code Layout

```
frontend/
├── index.html                          # Single page; 8 chart containers; population toggle
├── app.js                              # Entry — DataSource, renderers, toggle handler
├── styles.css
├── vendor/plotly.min.js                # Vendored Plotly.js v2.35.2
├── src/
│   ├── data_source.js                  # DataSource (fetch + schema guard) + FakeDataSource
│   ├── transforms.js                   # Pure formatSeries helpers
│   └── charts/                         # One module per chart (Strategy pattern)
└── tests/                              # Vitest suite (70 tests, no network/DOM)
src/
├── etl/
│   ├── common/
│   │   ├── object_store.py                  # ObjectStore protocol + S3ObjectStore / InMemoryObjectStore
│   │   ├── secrets_provider.py               # SecretsProvider protocol + SecretsManagerProvider / InMemorySecretsProvider
│   │   ├── notifier.py                       # Notifier protocol + SnsNotifier / InMemoryNotifier
│   │   └── tests/                            # pytest unit tests for the shared edge interfaces
│   ├── data_collection/
│   │   ├── idealista_listings_collector.py  # Thin Bronze Lambda handler (Factory + response shaping)
│   │   ├── bronze_collector.py               # BronzeCollector + SearchConfig strategies + IdealistaApiClient adapter
│   │   ├── requirements.txt                 # Runtime: requests, boto3
│   │   └── tests/                           # pytest unit + integration
│   ├── data_processing/
│   │   ├── silver_transform.py              # Pure Bronze→Silver transform (no AWS)
│   │   ├── silver_cleaner.py                 # SilverCleaner (list → read → clean → write)
│   │   ├── silver_cleaning_lambda.py        # Thin Silver Lambda handler (Factory)
│   │   ├── gold_aggregate.py                # Pure Silver→Gold aggregation helpers (no AWS)
│   │   ├── gold_aggregator.py                # Aggregation strategies + GoldAggregator
│   │   ├── gold_aggregation_lambda.py       # Thin Gold Lambda handler (Factory)
│   │   ├── backfill_silver.py               # CLI: fan-out silver lambda per snapshot_date
│   │   ├── requirements.txt                 # Runtime: boto3 (pandas via layer)
│   │   └── tests/                           # pytest unit + integration
│   ├── lambda_layers/                       # requests library as Lambda Layer
│   └── requirements-dev.txt                 # Dev: pytest, moto, black, ruff, mypy
└── notebooks/
    └── valenciaRealEstatePriceAnalysis.ipynb
```

## Getting Started

### Prerequisites

1. **AWS Account** with IAM permissions for: Lambda, S3, Secrets Manager, EventBridge, CloudWatch, SNS, IAM
2. **Terraform 1.14.3+**
3. **Python 3.12+**
4. **Idealista API Credentials** (two credential sets: LVW + PMV)

### Local Development Setup

```bash
# Clone the repository
git clone https://github.com/LeopoldWalther/vlc-market-pulse.git
cd vlc-market-pulse

# Create Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install all dev dependencies (testing, linting, type-checking)
pip install -r src/etl/requirements-dev.txt
# Also install runtime deps for local testing
pip install -r src/etl/data_collection/requirements.txt
pip install -r src/etl/data_processing/requirements.txt

# Run tests
cd src/etl
pytest data_collection/tests/ data_processing/tests/ -v --cov

# Pre-commit hooks (black, ruff, mypy, terraform fmt/validate, pytest)
pip install pre-commit
pre-commit install
```

### Infrastructure Deployment

```bash
# 1. Setup remote state (one-time only)
cd infrastructure/bootstrap
terraform init && terraform apply

# 2. Create secrets.tfvars (gitignored) with your API credentials
cd ../environments/dev
cat > secrets.tfvars << 'EOF'
idealista_api_key_lvw    = "your-lvw-api-key"
idealista_api_secret_lvw = "your-lvw-api-secret"
idealista_api_key_pmv    = "your-pmv-api-key"
idealista_api_secret_pmv = "your-pmv-api-secret"
notification_email       = "your@email.com"
EOF

# 3. Deploy dev environment (deploys all modules)
terraform init
terraform plan  -var-file="secrets.tfvars"
terraform apply -var-file="secrets.tfvars"
```

> **Note**: The dev Collector runs with `test_mode = true` (1 page per operation per week = 2 API calls total) to stay within Idealista API limits. Prod runs full collection.

## Deployment

### Dev vs. Prod Differences

| Setting | Dev | Prod |
|---|---|---|
| Collector `test_mode` | `true` — 1 page/op, no SNS mail | `false` — full collection |
| Silver Cleaner | deployed | prod wiring pending dev soak |
| `pandas_layer_arn` | `AWSSDKPandas-Python312:16` (eu-central-1 default) | same, set via variable |

### Manual Deploy

```bash
# Dev
cd infrastructure/environments/dev
terraform apply -var-file="secrets.tfvars"

# Prod
cd infrastructure/environments/prod
terraform apply -var-file="secrets.tfvars"
```

### Resources Created (Dev)

| Resource | Name |
|---|---|
| S3 Bucket | `dev-vlc-real-estate-analytics-listings` |
| Bronze Lambda | `dev-idealista-collector` |
| Silver Lambda | `dev-silver-cleaner` |
| EventBridge (bronze) | `dev-idealista-collector-weekly` — `cron(0 12 ? * SUN *)` |
| EventBridge (silver) | `dev-silver-cleaner-weekly` — `cron(30 12 ? * SUN *)` |
| Log Groups | `/aws/lambda/dev-idealista-collector`, `/aws/lambda/dev-silver-cleaner` |
| Secrets | `dev/idealista/lvw-api-credentials`, `dev/idealista/pmv-api-credentials` |
| SNS Topic | `dev-idealista-notifications` |
| CW Alarm | `dev-silver-cleaner-errors` |

### Invoke Manually

```bash
# Bronze Collector — full run (prod)
aws lambda invoke \
  --function-name prod-idealista-collector \
  --region eu-central-1 \
  response.json && cat response.json | jq .

# Bronze Collector — test run (1 page each)
aws lambda invoke \
  --function-name dev-idealista-collector \
  --region eu-central-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"test_mode": true}' \
  response.json

# Silver Cleaner — trigger manually
aws lambda invoke \
  --function-name dev-silver-cleaner \
  --region eu-central-1 \
  response.json && cat response.json | jq .

# Check CloudWatch logs
aws logs tail /aws/lambda/dev-silver-cleaner --region eu-central-1 --follow

# Verify silver Parquet files in S3
aws s3 ls s3://dev-vlc-real-estate-analytics-listings/silver/idealista/ \
  --recursive --region eu-central-1
```

## Testing

### Python Tests

```bash
cd src/etl

# All tests with coverage
pytest data_collection/tests/ data_processing/tests/ \
  --cov=data_collection --cov=data_processing \
  --cov-report=term-missing -v

# Individual suites
pytest data_collection/tests/ -v       # Bronze Collector (26 tests)
pytest data_processing/tests/ -v       # Silver transform + handler (26 tests)
```

### Infrastructure Tests

```bash
cd infrastructure/environments/dev
terraform fmt -check
terraform validate
terraform plan -var-file="secrets.tfvars"
```

## Monitoring & Alerting

| Signal | Where | Action |
|---|---|---|
| Bronze Lambda error | CloudWatch Logs | SNS email (prod only) |
| Silver Lambda error | CW Alarm `dev-silver-cleaner-errors` | SNS topic → email |
| No silver output | Check S3 prefix | Re-invoke manually |

```bash
# Tail logs
aws logs tail /aws/lambda/prod-idealista-collector --region eu-central-1 --follow
aws logs tail /aws/lambda/dev-silver-cleaner       --region eu-central-1 --follow
```

## Security

- **API Credentials**: Stored in AWS Secrets Manager, never in code or git
- **S3 Encryption**: AES-256 at rest
- **IAM Least Privilege**: Silver Cleaner reads only `bronze/idealista/*`, writes only `silver/*`
- **`secrets.tfvars`**: Excluded from version control via `.gitignore`
- **Log Retention**: 30 days on all CloudWatch log groups

## Cost Estimate

| Service | Dev | Prod |
|---|---|---|
| Lambda (2 functions × 4 invocations/month) | < $0.01 | < $0.01 |
| S3 (JSON + Parquet storage) | < $0.50 | < $0.50 |
| Secrets Manager (4 secrets) | ~$1.60 | ~$1.60 |
| CloudWatch Logs | < $0.50 | < $0.50 |
| SNS | < $0.01 | < $0.01 |
| **Total** | **~$2–3/month** | **~$2–3/month** |

## Troubleshooting

| Problem | Solution |
|---|---|
| Terraform state lock | `terraform force-unlock <LOCK_ID>` |
| Lambda times out | Check CloudWatch logs; silver cleaner needs pandas layer ARN |
| Silver Parquet missing | Run silver cleaner manually; check bronze prefix has data |
| API rate limit | Dev uses `test_mode`; prod rotates LVW/PMV credentials |
| `terraform plan` from wrong dir | Must run from `infrastructure/environments/dev` or `prod` |

## Documentation

- [DATA_COLLECTION_LAYER.md](documentation/DATA_COLLECTION_LAYER.md) — Bronze Collector architecture
- [DATA_PROCESSING_LAYER.md](documentation/DATA_PROCESSING_LAYER.md) — Silver Cleaner architecture
- [PIPELINE_HEALTH_LAYER.md](documentation/PIPELINE_HEALTH_LAYER.md) — Pipeline Health monitoring (traffic-light tab, Ampel rules, deployment & manual validation)

## Contributing

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Write tests first (TDD: RED → GREEN → REFACTOR)
3. Ensure all hooks pass: `pre-commit run --all-files`
4. Open a Pull Request

## License

MIT License — see [LICENSE](LICENSE).

---

**Last Updated**: 2026-09-10
