# Prod Promotion Runbook — FEATURE-006

This runbook covers the one-time steps to bring the prod environment fully online after
`terraform apply`. It **must** be executed in the exact order below. Deploying the frontend before
gold contains the full history produces near-empty charts (the same issue hit in dev — see
FEATURE-005 review M3).

## Prerequisites

- `terraform apply` in `infrastructure/environments/prod/` has completed successfully.
- The prod S3 listings bucket (`prod-vlc-real-estate-analytics-listings`) already contains all
  historical bronze snapshots collected since data collection began.
- AWS credentials with permission to invoke Lambda and list/read S3 objects are active in the
  shell.

## Step 1 — Silver backfill

Run the silver backfill script against the **prod** bucket and **prod** Lambda. This fans out one
`prod-silver-cleaner` invocation per `(operation, snapshot_date)` found in the bronze layer.

```bash
python src/etl/data_processing/backfill_silver.py \
  --bucket prod-vlc-real-estate-analytics-listings \
  --function-name prod-silver-cleaner
```

This may take 20–40 minutes depending on the number of historical snapshots. Monitor progress in
CloudWatch Logs (`/aws/lambda/prod-silver-cleaner`).

## Step 2 — Verify silver parquet count

Before proceeding, confirm the silver layer is complete. The parquet count must match the number
of distinct `(operation, snapshot_date)` pairs in the bronze layer.

```bash
# Count silver parquets for each operation
aws s3 ls s3://prod-vlc-real-estate-analytics-listings/silver/idealista/operation=sale/ \
  --recursive | grep part.parquet | wc -l

aws s3 ls s3://prod-vlc-real-estate-analytics-listings/silver/idealista/operation=rent/ \
  --recursive | grep part.parquet | wc -l

# Count distinct bronze snapshot dates for each operation (expected to match)
aws s3 ls s3://prod-vlc-real-estate-analytics-listings/bronze/idealista/ \
  | grep sale_ | awk '{print $4}' | cut -d_ -f2 | sort -u | wc -l

aws s3 ls s3://prod-vlc-real-estate-analytics-listings/bronze/idealista/ \
  | grep rent_ | awk '{print $4}' | cut -d_ -f2 | sort -u | wc -l
```

**Do not proceed to step 3 until the silver parquet count equals the expected bronze snapshot
count for both operations.**

## Step 3 — Invoke gold aggregator

Once silver is fully populated, run the gold aggregator to produce `gold/aggregations/latest.json`
with the complete sale + rent history.

```bash
aws lambda invoke \
  --function-name prod-gold-aggregator \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/prod_gold.json \
  --region eu-central-1

cat /tmp/prod_gold.json
```

Expected output: `{"statusCode": 200, "key": "gold/aggregations/latest.json", "bytes": ...}`.

## Step 4 — Verify gold date coverage

Confirm that `latest.json` contains the full history for both operations before deploying the
frontend:

```bash
aws s3 cp s3://prod-vlc-real-estate-analytics-listings/gold/aggregations/latest.json - \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
gen = data['general']
ts = gen.get('price_time_series_neighborhood', [])
sale_dates = {p['snapshot_date'] for p in ts if p.get('operation') == 'sale'}
rent_dates = {p['snapshot_date'] for p in ts if p.get('operation') == 'rent'}
print(f'Sale dates: {len(sale_dates)}')
print(f'Rent dates: {len(rent_dates)}')
print(f'Schema:     {data[\"schema_version\"]}')
"
```

The counts should match the silver parquet counts from step 2.

## Step 5 — Deploy frontend to prod

Trigger the deploy workflow via GitHub Actions:

1. Go to **Actions → Deploy Frontend → Run workflow**.
2. Select `environment: prod`.
3. The workflow runs `npm test`, syncs assets to `prod-vlc-frontend-assets`, and invalidates
   CloudFront.

Or run manually:

```bash
# Read bucket + distribution from Terraform outputs
cd infrastructure/environments/prod
ASSET_BUCKET=$(terraform output -raw frontend_asset_bucket_name)
DISTRIBUTION_ID=$(terraform output -raw frontend_distribution_id)

# Sync (excludes tests, node_modules, coverage, *.test.js)
aws s3 sync frontend/ s3://${ASSET_BUCKET}/ \
  --exclude "tests/*" \
  --exclude "node_modules/*" \
  --exclude "coverage/*" \
  --exclude "*.test.js" \
  --delete

# Invalidate CloudFront cache
aws cloudfront create-invalidation \
  --distribution-id ${DISTRIBUTION_ID} \
  --paths "/*"
```

## Step 6 — Smoke test

Open https://vlc-market-pulse.leopoldwalther.com and verify:

- All eight charts render with data.
- The population toggle appears and switches between "All listings" and the filtered population.
- Chart titles update on toggle.
- No browser console errors.

## Notes

- The prod `terraform apply` is a **gated, manual step** — do not automate it. Review
  `terraform plan` output before applying.
- Prod issues its own ACM certificate for `vlc-market-pulse.leopoldwalther.com` (us-east-1). No
  shared DNS stack has to be applied first. The `leopoldwalther.com` zone is owned by the
  `leopoldwalther-website` repository, so never create apex or zone-wide records here.
- If FEATURE-007 (Step Functions orchestration) has already landed, **do not** wire the per-Lambda
  EventBridge crons into prod. Wire the `pipeline_orchestrator` state machine instead and set
  `create_schedule = false` on the bronze/silver/gold modules.
- The weekly EventBridge schedules (cron bronze 12:00, silver 12:30, gold 12:45 UTC Sundays) are
  created automatically by `terraform apply` — no manual trigger needed after the initial backfill.
