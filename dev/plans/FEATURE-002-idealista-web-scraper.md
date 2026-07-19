# FEATURE-002: Idealista Web Scraper — OOP Service on AWS Fargate

**Status:** 🟡 In Progress (Phase 1 of 3 complete — OOP core + local notebook validated; Docker/Fargate phases pending)
**Branch:** `feature/idealista-web-scraper`
**Assignee:** @implementer
**Created:** 2026-03-29
**Updated:** 2026-06-06
**Estimated Effort:** L (6–8 days)
**Priority:** High

---

## Objective

Supplement the Idealista API collector (which is capped at 100 listings/month) with a web scraper that can retrieve unlimited search-result listings. Deliver two artefacts: (1) a Jupyter notebook for local, interactive development and testing, and (2) a containerized production service (Docker image on **AWS ECS Fargate**) that runs weekly and scrapes **all** Valencia sale and rent listings.

---

## Context

The existing `idealista_listings_collector.py` Lambda uses the Idealista API (OAuth2). The API imposes monthly request quotas that limit how many listings can be collected. The public Idealista website serves the same data without programmatic limits. A scraper targeting the public search-results pages closes this gap and provides a second, parallel data source in the same S3 bronze layer.

**Why a container on Fargate instead of a Lambda?** Scraping *all* Valencia listings (thousands of cards across hundreds of pages) behind rotating proxies is a long-running, network-heavy job. Lambda's 15-minute ceiling and packaging limits (large native deps such as `lxml`, headless browser tooling) make it a poor fit. A Docker container on **ECS Fargate**, triggered weekly by EventBridge Scheduler, has no hard runtime cap, ships its own dependencies, and scales memory/CPU independently.

**Existing project patterns to follow:**
- `src/etl/data_collection/idealista_listings_collector.py` → class structure, custom exceptions, dependency injection of AWS clients, full type hints + docstrings
- Medallion architecture: raw output → `bronze/idealista-scraper/{YYYY-MM-DD}/` in the existing S3 listings bucket
- Reuse existing Terraform `s3/` and `sns/` modules; no Secrets Manager for the API (the scraper needs none) — but a new secret holds the **proxy provider** credentials

**Engineering standard for this feature:** the entire codebase is **object-oriented**, applying the four pillars of OOP (abstraction, encapsulation, inheritance, polymorphism), the **SOLID** principles, and explicit **design patterns** (Strategy, Repository, Adapter, Factory, Template Method, Builder). Each listing is modelled as a rich domain object rather than a bare dict.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  EventBridge Scheduler   rate: weekly  cron(0 13 ? * SUN *)           │
│  (1 h after the API collector at 12:00)                              │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ RunTask
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  AWS ECS Fargate task  (Docker image pulled from ECR)                │
│                                                                      │
│   ScrapeOrchestrator                                                 │
│     ├─ OperationStrategy[sale|rent]   (Strategy)                     │
│     ├─ SearchUrlBuilder               (Builder)                      │
│     ├─ ProxyProvider  ◄── RayobyteProxyProvider | ProxyRackProvider  │
│     │                     (Adapter + Factory, rotating IPs)          │
│     ├─ PageFetcher     (cloudscraper + retry/backoff via proxy)      │
│     ├─ ListingParser   (BeautifulSoup + lxml → Listing objects)      │
│     └─ ListingRepository ◄── S3ListingRepository | LocalRepository   │
│                              (Repository pattern)                    │
└───────────────┬──────────────────────────────┬───────────────────────┘
                │ PutObject                     │ Publish on failure
                ▼                               ▼
┌───────────────────────────────┐   ┌───────────────────────────────┐
│  S3 listings bucket           │   │  SNS topic (shared with API)  │
│  bronze/idealista-scraper/    │   │  error alerts                 │
│    {YYYY-MM-DD}/              │   └───────────────────────────────┘
│      {operation}_page{N}.json │
└───────────────────────────────┘
        ▲                                   ▲
        │ proxy creds                       │ logs
┌───────────────────────────────┐   ┌───────────────────────────────┐
│  Secrets Manager              │   │  CloudWatch Logs              │
│  proxy provider credentials   │   │  /ecs/{env}-idealista-scraper │
└───────────────────────────────┘   └───────────────────────────────┘
```

Two delivery layers share **the same OOP core package** (`src/etl/data_collection/scraper/`):

1. **Local / notebook** — a Jupyter notebook and a `python -m scraper` CLI drive the core classes against `LocalListingRepository`, writing to `data/s3/` with no AWS credentials. Used for interactive selector development and validation.
2. **Production** — the identical core runs inside a Docker container on ECS Fargate, wired to `S3ListingRepository`, a real `ProxyProvider`, CloudWatch, and SNS.

---

## OOP Design

The whole feature is object-oriented. The core package exposes small, single-responsibility abstractions (Protocols / ABCs) and concrete implementations injected at the composition root.

### Domain model (encapsulation)

```python
class Listing:
    """One real-estate listing. Validates and normalises its own fields."""
    # private, validated attributes; read-only properties; to_dict() → camelCase
class ListingCollection:
    """Aggregate of Listing objects for one (operation, snapshot, page)."""
```

`Listing` is a rich domain object (not a bare dict): it validates types in `__init__`, normalises price/size strings to numbers, exposes read-only properties, and serialises via `to_dict()` to the camelCase envelope that matches the API `elementList` schema.

### Abstractions (DIP + ISP) and the patterns behind them

| Abstraction (interface) | Concrete impls | Pattern | SOLID driver |
|---|---|---|---|
| `OperationStrategy` | `SaleStrategy`, `RentStrategy` | **Strategy** | OCP — add operations without touching the orchestrator |
| `SearchUrlBuilder` | `IdealistaUrlBuilder` | **Builder** | SRP — URL assembly isolated from fetching |
| `ProxyProvider` | `RayobyteProxyProvider`, `ProxyRackProvider`, `NullProxyProvider` | **Adapter + Factory** | DIP/LSP — providers are interchangeable; chosen by `ProxyProviderFactory` from config |
| `PageFetcher` | `CloudscraperFetcher` | **Template Method** (fetch→retry→backoff) | SRP — transport concerns isolated |
| `ListingParser` | `IdealistaListingParser` | — (uses `DOM_SELECTORS` map) | OCP — selector changes are data, not code |
| `ListingRepository` | `S3ListingRepository`, `LocalListingRepository` | **Repository** | DIP — storage target swappable (S3 vs disk) |

`ScrapeOrchestrator` is the high-level policy object. It depends only on the abstractions above (constructor injection), so it is fully unit-testable with fakes and satisfies the **four pillars**: abstraction (interfaces), encapsulation (`Listing`), inheritance (base fetcher/provider), polymorphism (strategy/provider/repository swap).

---

## Scope & Scraped Fields

**Scope:** scrape **all** Valencia listings for **both** operations — sale (`venta-viviendas`) and rent (`alquiler-viviendas`) — with **no size/elevator filter** (broader than the API collector by design). The English site is the entry point, e.g. `https://www.idealista.com/en/venta-viviendas/valencia-valencia/`.

The parser extracts the following from each search-result card, matching the API `elementList` schema as closely as possible:

| Field | API key | Source on page |
|---|---|---|
| Listing ID | `propertyCode` | URL slug `/inmueble/{id}/` |
| Thumbnail | `thumbnail` | `img.item-multimedia` src |
| Price | `price` | `.item-price` text |
| Size (m²) | `size` | `.item-detail` containing "m²" |
| Rooms | `rooms` | `.item-detail` containing "hab." / "bed" |
| Floor | `floor` | `.item-detail` containing "planta" / "floor" |
| Address | `address` | `.item-address` text |
| Neighborhood | `neighborhood` | `.item-address` span |
| Price/m² | `priceByArea` | `.item-price-down` or computed `price / size` |
| Listing URL | `url` | `article.item a.item-link` href |
| Operation | `operation` | Injected by the active `OperationStrategy` |

*Note: `latitude`, `longitude`, `district`, `bathrooms`, `description` are generally not present on search-result cards (detail pages are out of MVP scope) and are set to `null`. Detail-page enrichment is a documented follow-up.*

---

## Target URLs

```
# Sale (venta) — English site, all Valencia
https://www.idealista.com/en/venta-viviendas/valencia-valencia/?pagina=N

# Rent (alquiler) — English site, all Valencia
https://www.idealista.com/en/alquiler-viviendas/valencia-valencia/?pagina=N
```

`IdealistaUrlBuilder` owns this template; the page number `N` and operation segment come from the active `OperationStrategy`. No size/elevator filter segments are applied (full inventory).

---

## Phase 1 — OOP core package + local validation

### Objective
Build the object-oriented scraper core and validate it interactively: confirm `cloudscraper` (behind a proxy) bypasses bot detection, identify the correct CSS selectors, extract all target fields into `Listing` objects, and write to local `data/s3/` with `LocalListingRepository` — no AWS credentials required.

### Step 1.1: Dependencies & package skeleton
- Create the package `src/etl/data_collection/scraper/` with `__init__.py` and submodules: `domain.py`, `urls.py`, `proxies.py`, `fetcher.py`, `parser.py`, `repository.py`, `orchestrator.py`, `config.py`, `errors.py`, `factory.py`, `__main__.py` (CLI).
- Add to `src/etl/requirements-dev.txt`: `cloudscraper>=1.2.71`, `beautifulsoup4>=4.12.0`, `lxml>=5.1.0`.
- Create `src/etl/data_collection/scraper/requirements.txt` (runtime deps for the Docker image).

### Step 1.2: Domain model (`domain.py`) — encapsulation
- `Listing`: validated, encapsulated entity; normalises price/size; read-only properties; `to_dict()` → camelCase API envelope keys; `__eq__`/`__hash__` on `propertyCode` for dedup.
- `ListingCollection`: aggregate with `add()`, `to_envelope(operation, page, total_pages)` producing `{"operation", "source": "web_scraper", "collected_at", "page", "totalPages", "elementList": [...]}`.

### Step 1.3: Abstractions & strategies
- `errors.py`: `ScraperError` base + `FetchError`, `ParseError`, `ProxyError`.
- `OperationStrategy` (ABC) with `SaleStrategy`/`RentStrategy` (URL segment + `operation` label).
- `IdealistaUrlBuilder` (Builder) consuming an `OperationStrategy` + page number.
- `ProxyProvider` (ABC): `get_proxy() -> dict | None`, `rotate()`. Impls: `RayobyteProxyProvider`, `ProxyRackProvider`, `NullProxyProvider` (local/no-proxy). `ProxyProviderFactory` selects by config.
- `PageFetcher` (Template Method): `fetch(url)` orchestrates proxy selection → request → retry/backoff; `CloudscraperFetcher` implements the transport step.
- `ListingParser` (ABC) + `IdealistaListingParser` using a module-level `DOM_SELECTORS` map; helpers `_parse_price`, `_parse_size`, `_parse_rooms`, `_extract_property_code`.
- `ListingRepository` (ABC): `save(collection, operation, page)`. Impls: `LocalListingRepository` (writes `data/s3/...`), `S3ListingRepository` (boto3 `put_object`).

### Step 1.4: Orchestrator (`orchestrator.py`) — high-level policy (DIP)
- `ScrapeOrchestrator(fetcher, parser, repository, proxy_provider, url_builder)` injected via constructor.
- `scrape(operation_strategy) -> int`: paginates until an empty/last page, parses each page into a `ListingCollection`, persists via the repository, rotates the proxy and sleeps `random.uniform(2.0, 4.5)` between pages.
- No I/O concretions referenced directly — only the injected abstractions (fully unit-testable with fakes).

### Step 1.5: Local testing notebook + CLI for validation
The notebook is the primary **learning and testing surface** for Phase 1 — it walks through the scrape *step by step* so a contributor can see exactly what each component does and inspect intermediate results before anything is containerised. `src/notebooks/idealista_web_scraper.ipynb` is structured as a guided sequence of cells:

1. **Setup** — import the core package; instantiate `NullProxyProvider` + `LocalListingRepository` (no AWS, no proxy creds).
2. **Fetch one page** — build a URL via `IdealistaUrlBuilder` for `sale`, fetch the raw HTML with `CloudscraperFetcher`, and print status + a snippet to confirm bot-detection is bypassed.
3. **Parse & inspect** — run `IdealistaListingParser` on that HTML, render the resulting `Listing` objects as a `pd.DataFrame`, and eyeball the extracted fields (price, size, rooms, address…).
4. **Full run** — drive `ScrapeOrchestrator` across all pages for `sale` then `rent`, writing JSON to `data/s3/` via `LocalListingRepository`.
5. **Validate** — load the written JSON back, preview as a `DataFrame`, and compare field coverage against an existing API `data/s3/*.json` so schema drift is obvious.

Each cell has a short markdown explanation of *what* and *why*, so the notebook doubles as living documentation of the scraping flow.
- `python -m etl.data_collection.scraper --operation sale --output-dir data/s3/` mirrors the notebook headlessly for quick re-runs and CI smoke tests.

### Step 1.6: Validate locally & capture fixtures
- Run end-to-end; confirm listings retrieved across multiple pages for both operations.
- Spot-check 3 listings against live Idealista.
- Save a **real** HTML snapshot to `src/etl/data_collection/tests/fixtures/search_results_sale.html` (≥ 3 cards) for deterministic parser tests.

---

## Phase 2 — Containerization (Docker + ECR)

### Objective
Package the validated core into a reproducible Docker image that runs the scraper as a one-shot task, ready for Fargate.

### Step 2.1: Entry point
- `src/etl/data_collection/scraper/run_task.py`: the container entry point (`main()`). Reads env config, builds the production object graph via `ProxyProviderFactory` + `S3ListingRepository`, scrapes both operations, publishes an SNS alert on any unhandled `ScraperError`, exits non-zero on failure (so ECS marks the task failed).
- Config (`config.py`) reads env: `S3_BUCKET`, `S3_PREFIX=bronze/idealista-scraper/`, `SNS_TOPIC_ARN`, `PROXY_PROVIDER` (`rayobyte`|`proxyrack`|`none`), `PROXY_SECRET_NAME`, `AWS_REGION`.

### Step 2.2: Dockerfile
- `src/etl/data_collection/scraper/Dockerfile` based on `python:3.12-slim`; installs `requirements.txt` (incl. `lxml` system libs), copies the package, sets `CMD ["python", "-m", "etl.data_collection.scraper.run_task"]`. Non-root user; pinned deps.
- `.dockerignore` to keep the image small.

### Step 2.3: Tests
- `src/etl/data_collection/tests/test_scraper/` package mirroring the core modules:

| Test module | Covers |
|---|---|
| `test_domain.py` | `Listing` validation/normalisation, `to_dict()` keys, dedup equality |
| `test_urls.py` | `IdealistaUrlBuilder` for sale/rent + pagination |
| `test_proxies.py` | Factory selection; `Rayobyte`/`ProxyRack` adapters build correct proxy dict; rotation |
| `test_fetcher.py` | Retry/backoff on 429/503 (mocked), proxy passed through |
| `test_parser.py` | Parse the **real** HTML fixture → correct `Listing` fields |
| `test_repository.py` | `S3ListingRepository.save` calls `put_object` with correct key/body (moto); `Local` writes file |
| `test_orchestrator.py` | Full scrape with fakes: pagination stop, proxy rotation, repository calls |
| `test_run_task.py` | Env validation, SNS publish on error, non-zero exit |

- Coverage ≥ 80% for the `scraper` package.

---

## Phase 3 — Fargate infrastructure, scheduling & proxies

### Objective
Provision the ECS Fargate task, weekly schedule, ECR repository, proxy secret, IAM, logging, and alerting; wire it into `dev` (prod deferred until after a dev soak, mirroring FEATURE-003).

### Step 3.1: Terraform module `infrastructure/modules/fargate_scraper/`
- **ECR** repository for the image (lifecycle policy to expire untagged images).
- **ECS cluster** (or reuse a shared one) + **Fargate task definition**: `cpu=512`, `memory=1024`, `python3.12` image, log driver `awslogs`.
- **EventBridge Scheduler** rule `cron(0 13 ? * SUN *)` with an **ECS RunTask** target (1 h after the API collector).
- **Networking:** task runs in a subnet with outbound internet (public subnet + assign public IP, or private subnet + NAT) so it can reach Idealista and the proxy endpoints.
- **IAM:** task execution role (ECR pull, CloudWatch Logs) + task role scoped to `s3:PutObject` on `bronze/idealista-scraper/*`, `sns:Publish` on the topic, and `secretsmanager:GetSecretValue` on the proxy secret only.
- **CloudWatch** log group `/ecs/{env}-idealista-scraper` (30-day retention) + SNS error alarm.
- `variables.tf` / `outputs.tf`; region-aware; no hardcoded ARNs.

### Step 3.2: Proxy provider secret
- New Terraform `secrets` usage (or extend the module) holding the proxy provider credentials (Rayobyte/ProxyRack endpoint + username/password/API key). Never committed; injected via `secrets.tfvars`.

### Step 3.3: Wire dev + image build/push docs
- Instantiate `module "idealista_scraper"` in `infrastructure/environments/dev/main.tf` (passing bucket, SNS topic, proxy secret name).
- Add a documented build/push flow (`docker build` → `aws ecr get-login-password` → `docker push`) — manual for now; a `deploy-scraper.yml` CI workflow is a deferred follow-up.
- `terraform fmt -check` + `terraform validate` pass in `dev`.

### Step 3.4: Docs
- Extend `documentation/DATA_COLLECTION_LAYER.md` with the scraper architecture, the proxy abstraction, the `bronze/idealista-scraper/` layout, and operational runbook (rotate proxy creds, re-run task).

---

## Files to Modify / Create

| File | Action | Purpose |
|---|---|---|
| `src/etl/data_collection/scraper/__init__.py` | **CREATE** | Package marker + public exports |
| `src/etl/data_collection/scraper/domain.py` | **CREATE** | `Listing`, `ListingCollection` domain objects |
| `src/etl/data_collection/scraper/errors.py` | **CREATE** | `ScraperError` hierarchy |
| `src/etl/data_collection/scraper/urls.py` | **CREATE** | `OperationStrategy`, `IdealistaUrlBuilder` |
| `src/etl/data_collection/scraper/proxies.py` | **CREATE** | `ProxyProvider` + Rayobyte/ProxyRack/Null + factory |
| `src/etl/data_collection/scraper/fetcher.py` | **CREATE** | `PageFetcher` / `CloudscraperFetcher` |
| `src/etl/data_collection/scraper/parser.py` | **CREATE** | `ListingParser`, `DOM_SELECTORS` |
| `src/etl/data_collection/scraper/repository.py` | **CREATE** | `ListingRepository` + S3/Local impls |
| `src/etl/data_collection/scraper/orchestrator.py` | **CREATE** | `ScrapeOrchestrator` high-level policy |
| `src/etl/data_collection/scraper/config.py` | **CREATE** | Env-driven config object |
| `src/etl/data_collection/scraper/factory.py` | **CREATE** | Composition root (build object graph) |
| `src/etl/data_collection/scraper/__main__.py` | **CREATE** | Local CLI entry point |
| `src/etl/data_collection/scraper/run_task.py` | **CREATE** | Fargate container entry point |
| `src/etl/data_collection/scraper/requirements.txt` | **CREATE** | Runtime deps for the Docker image |
| `src/etl/data_collection/scraper/Dockerfile` | **CREATE** | Container image definition |
| `src/etl/data_collection/scraper/.dockerignore` | **CREATE** | Keep image small |
| `src/etl/data_collection/tests/test_scraper/*.py` | **CREATE** | Unit/integration tests per module |
| `src/etl/data_collection/tests/fixtures/search_results_sale.html` | **CREATE** | Real HTML fixture for parser tests |
| `src/notebooks/idealista_web_scraper.ipynb` | **CREATE** | Phase 1 interactive validation notebook |
| `src/etl/requirements-dev.txt` | **MODIFY** | Add cloudscraper, bs4, lxml for local dev |
| `infrastructure/modules/fargate_scraper/main.tf` | **CREATE** | ECR + ECS Fargate + Scheduler + IAM + logs |
| `infrastructure/modules/fargate_scraper/variables.tf` | **CREATE** | Module input variables |
| `infrastructure/modules/fargate_scraper/outputs.tf` | **CREATE** | Module outputs |
| `infrastructure/environments/dev/main.tf` | **MODIFY** | Instantiate `fargate_scraper` + proxy secret |
| `documentation/DATA_COLLECTION_LAYER.md` | **MODIFY** | Add scraper architecture + runbook |

**Forbidden / out of scope:**
- Do NOT modify `idealista_listings_collector.py` or its tests
- Do NOT modify the existing `lambda_bronze` / `lambda_silver` Terraform modules
- Do NOT wire the scraper into `prod` yet (deferred until after a dev soak, mirroring FEATURE-003)
- Do NOT add CI/CD deploy workflows (a `deploy-scraper.yml` pipeline is a separate future task)
- Detail-page enrichment (lat/long, bathrooms, description) is out of MVP scope

---

## Testing Requirements

- [ ] `test_parser.py` passes against the **real** HTML fixture (not hand-crafted HTML)
- [ ] `test_orchestrator.py` confirms pagination stops at the last/empty page and the proxy rotates between pages
- [ ] `test_proxies.py` confirms the factory returns the right provider and adapters build a correct proxy dict
- [ ] `test_repository.py` confirms `S3ListingRepository` writes the correct `bronze/idealista-scraper/{date}/{operation}_page{N}.json` key (moto)
- [ ] `test_run_task.py` confirms SNS publish + non-zero exit on `ScraperError`
- [ ] Notebook runs end-to-end locally with `NullProxyProvider` (manual smoke test)
- [ ] Output JSON envelope `elementList` matches the field names used by `valenciaRealEstatePriceAnalysis.ipynb`
- [ ] `pytest --cov=src/etl/data_collection/scraper` coverage ≥ 80%

---

## Success Criteria

- [ ] Core package runs locally via `python -m etl.data_collection.scraper --operation sale --output-dir data/s3/` with no AWS credentials
- [ ] Notebook produces valid JSON for both sale and rent
- [ ] Docker image builds and runs the one-shot task locally (`docker run` with env vars against a test bucket / local repo)
- [ ] All unit/integration tests pass with mocked AWS/HTTP/proxy; coverage ≥ 80%
- [ ] `terraform fmt -check` + `terraform validate` pass in `dev`
- [ ] Proxy provider is swappable via config (`PROXY_PROVIDER=rayobyte|proxyrack|none`) without code changes (OCP verified by test)
- [ ] All code: OOP with SOLID + documented patterns, full type hints, docstrings, ruff + black + mypy pass

---

## Estimated Monthly AWS Cost

The scraper runs as a **weekly one-shot Fargate task** (~4.33 runs/month). Assuming each run takes ~2 h to scrape both operations across the full Valencia inventory (with the randomized 2–4.5 s inter-page delays), the new AWS components cost roughly **~$1/month** in `dev` (region `eu-central-1`). The dominant fixed cost is the proxy secret; compute is negligible because the task is short-lived.

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| **ECS Fargate task** | $0.04656 / vCPU-h + $0.00511 / GB-h | 0.5 vCPU + 1 GB × 2 h × 4.33 runs | ~$0.25 |
| **Secrets Manager** | $0.40 / secret + $0.05 / 10k calls | 1 proxy secret, few reads | ~$0.40 |
| **ECR storage** | $0.10 / GB-month | ~300 MB image (lifecycle expires old tags) | ~$0.03 |
| **EventBridge Scheduler** | $1.00 / million invocations | ~4.33 invocations | <$0.01 |
| **CloudWatch Logs** | ~$0.57 / GB ingest + $0.03 / GB stored | few MB/run, 30-day retention | ~$0.05 |
| **S3 (bronze)** | $0.023 / GB-month + PUT requests | incremental JSON (~hundreds of MB/yr) | ~$0.05 |
| **Public IPv4 (while task runs)** | $0.005 / IP-h | only billed during the ~2 h run | ~$0.04 |
| **Data transfer in** | free (inbound) | downloading search pages | $0.00 |
| **Total (new AWS components, dev)** | | | **~$0.85/month** |

**Cost drivers & decisions:**
- **Networking — public subnet + public IP (recommended for dev).** A **NAT Gateway** would add **~$32–33/month** (≈$0.045/h × 730 h + per-GB processing) *regardless of how little the task runs*, because it is billed continuously. Assigning a public IP to the Fargate task in a public subnet avoids this entirely and keeps the public-IP charge to ~$0.04/month (only while running). This preserves the project's "< $5/month" target.
- **Non-AWS — proxy provider (external SaaS).** Rotating residential proxies (Rayobyte / ProxyRack) are billed *outside* AWS, typically **~$5–15/month** depending on plan/bandwidth. This is the largest real cost of the feature but is not an AWS line item.
- Running the scraper in **`prod`** as well would roughly **double** the AWS figure (~$1.70/month total across both envs) — still well within budget.

> **Bottom line:** new AWS cost ≈ **$1/month** in `dev` (or ~$2/month dev + prod) as long as the Fargate task uses a public IP rather than a NAT Gateway. The external proxy subscription (~$5–15/month) is the dominant overall cost.

---

## Technical Notes

### Bot detection & rotating proxies (Rayobyte / ProxyRack)
Idealista uses Cloudflare and aggressive fingerprinting; a single IP scraping the full inventory will be blocked. Mitigations layered in the `PageFetcher` + `ProxyProvider`:
- **Rotating residential proxies** via `RayobyteProxyProvider` or `ProxyRackProvider` (selected by `PROXY_PROVIDER`); credentials read from Secrets Manager. `ProxyProvider.rotate()` is called between pages so each request can use a fresh exit IP.
- `cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})` for a realistic UA; add `Referer` and `Accept-Language` headers.
- Random delay `random.uniform(2.0, 4.5)` between pages; exponential backoff with proxy rotation on HTTP 429/403/503.
- `NullProxyProvider` is used locally (notebook/dev) so contributors can run without proxy credentials.

### DOM selectors strategy
All CSS selectors live in a module-level `DOM_SELECTORS` map in `parser.py`. When Idealista changes markup (2–3×/year), only this map changes — code stays closed for modification (OCP):

```python
DOM_SELECTORS: Dict[str, str] = {
    "listing_cards":  "article.item",
    "link":           "a.item-link",
    "price":          "span.item-price",
    "size":           "span.item-detail-char",
    "address":        "span.item-address",
    "neighborhood":   "span.item-address-name",
    "thumbnail":      "img.item-multimedia",
}
```

### JSON output envelope
`ListingCollection.to_envelope()` matches the API collector's S3 structure so downstream silver/gold code is source-agnostic:

```json
{
  "operation": "sale",
  "source": "web_scraper",
  "collected_at": "2026-06-07T13:00:00Z",
  "page": 1,
  "totalPages": 42,
  "elementList": [
    {
      "propertyCode": "12345678",
      "price": 250000.0,
      "size": 95.0,
      "rooms": 3,
      "floor": "2",
      "address": "Calle de Colón",
      "neighborhood": "El Mercat",
      "url": "https://www.idealista.com/inmueble/12345678/",
      "thumbnail": "https://img3.idealista.com/...",
      "operation": "sale",
      "priceByArea": 2631.0,
      "latitude": null,
      "longitude": null,
      "district": null,
      "bathrooms": null,
      "description": null
    }
  ]
}
```

### Docker image build & push
The container ships its own dependencies (no Lambda layer needed). `lxml` wheels install cleanly on `python:3.12-slim` (add `libxml2`/`libxslt` only if building from source):

```bash
docker build -t idealista-scraper src/etl/data_collection/scraper/
aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin <acct>.dkr.ecr.eu-central-1.amazonaws.com
docker tag idealista-scraper:latest <acct>.dkr.ecr.eu-central-1.amazonaws.com/dev-idealista-scraper:latest
docker push <acct>.dkr.ecr.eu-central-1.amazonaws.com/dev-idealista-scraper:latest
```

### Phase sequencing (critical path)
```
1.1 Package skeleton + deps
  └─▶ 1.2–1.4 Domain, abstractions, orchestrator (OOP core)
        └─▶ 1.5–1.6 Notebook/CLI validation + capture HTML fixture
              └─▶ 2.1–2.2 Container entry point + Dockerfile
                    └─▶ 2.3 Tests (≥80%)
                          └─▶ 3.1–3.2 Fargate + ECR + proxy secret (Terraform)
                                └─▶ 3.3–3.4 Wire dev + build/push + docs
```

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Idealista blocks scraping despite proxies | Medium | High | Rotating residential proxies (Rayobyte/ProxyRack), randomized delays, backoff; `ProxyProvider` is swappable to add more pools |
| DOM selectors change between plan and implementation | High | Medium | Capture real HTML fixture immediately; selectors isolated in `DOM_SELECTORS` |
| Proxy credentials leak / misconfig | Medium | High | Credentials only in Secrets Manager; task role scoped to one secret; never committed (`secrets.tfvars`) |
| Fargate networking can't reach internet | Medium | High | Validate subnet has NAT or public IP + IGW in `dev` before first run |
| `lxml` build issues in container | Low | Low | Use slim image with prebuilt wheels; add system libs only if needed |
| Scraping the full inventory is slow / costly | Medium | Low | Fargate has no 15-min cap; `cpu=512/mem=1024` is cheap for a weekly one-shot |
| Scraped data drifts from API schema | Medium | Medium | `to_dict()` keys asserted in `test_domain.py`; envelope matches API |

---

## Questions / Open Items

- **Proxy provider default:** start with one provider (recommend **Rayobyte** residential) and keep `ProxyRack` as a drop-in via the factory? *Recommendation: yes — both implemented, default chosen by config.*
- **Networking:** use the default VPC public subnet (assign public IP) or a dedicated private subnet + NAT for the Fargate task? *Recommendation: public subnet + public IP in `dev` for cost; revisit for `prod`.*
- **Both operations in one task vs two:** run sale + rent sequentially in a single weekly task (simpler, one image) — *recommended* — or split into two scheduled tasks? *Recommendation: single task, sequential.*
- **Detail-page enrichment:** defer lat/long/bathrooms to a follow-up feature? *Recommendation: yes, out of MVP scope.*

---

## Planning Summary (For Quick Reference)

**One-line objective:**
Build an OOP web scraper (SOLID + design patterns) that runs weekly as a Docker container on AWS Fargate behind rotating proxies, collecting **all** Valencia sale and rent listings into `bronze/idealista-scraper/`.

**Critical decisions:**
- Compute: **Docker on ECS Fargate** (not Lambda) — no runtime cap, ships own deps, fits long proxied scrape
- Architecture: object-oriented core (Strategy, Repository, Adapter, Factory, Template Method, Builder); `Listing` domain objects; DI composition root
- Anti-blocking: rotating residential proxies via `ProxyProvider` (Rayobyte/ProxyRack), creds in Secrets Manager
- Output: API-compatible JSON envelope under `bronze/idealista-scraper/{date}/{operation}_page{N}.json`
- Schedule: EventBridge `cron(0 13 ? * SUN *)` — 1 h after the API collector
- Local dev: `NullProxyProvider` + `LocalListingRepository`, no AWS creds needed

**Tasks at a glance:**

| Task | Priority | Est. | Dependencies |
|---|---|---|---|
| 1.1 Package skeleton + deps | P0 | 1 h | None |
| 1.2 Domain model (`Listing`/`ListingCollection`) | P0 | 2 h | 1.1 |
| 1.3 Abstractions + strategies + proxies | P0 | 4 h | 1.2 |
| 1.4 Orchestrator (DI) | P0 | 3 h | 1.3 |
| 1.5 Notebook + CLI validation | P0 | 3 h | 1.4 |
| 1.6 Capture HTML fixture | P0 | 1 h | 1.5 |
| 2.1 Container entry point | P0 | 2 h | 1.4 |
| 2.2 Dockerfile | P0 | 2 h | 2.1 |
| 2.3 Tests (≥80%) | P0 | 5 h | 2.1, 1.6 |
| 3.1 Fargate/ECR Terraform module | P1 | 5 h | None |
| 3.2 Proxy secret | P1 | 1 h | 3.1 |
| 3.3 Wire dev + build/push | P1 | 2 h | 2.2, 3.1 |
| 3.4 Docs | P1 | 1 h | 3.3 |

**Key files to create:**
- [src/etl/data_collection/scraper/](src/etl/data_collection/scraper/) — OOP core package
- [src/etl/data_collection/scraper/Dockerfile](src/etl/data_collection/scraper/Dockerfile) — container image
- [infrastructure/modules/fargate_scraper/main.tf](infrastructure/modules/fargate_scraper/main.tf) — ECS Fargate + ECR + Scheduler
- [src/notebooks/idealista_web_scraper.ipynb](src/notebooks/idealista_web_scraper.ipynb) — local validation

**Watch-outs for reviewer:**
- Proxy credentials must come from Secrets Manager only, never committed; task role scoped to one secret
- Fargate subnet must have outbound internet (public IP or NAT) before the first run
- `Listing.to_dict()` keys must match the camelCase used by `valenciaRealEstatePriceAnalysis.ipynb`
- Test HTML fixture must be a real Idealista page snapshot, not hand-crafted
- Keep the OOP core free of AWS/HTTP concretions — only the composition root wires concrete impls

**Blockers / open questions:**
- Confirm proxy vendor account (Rayobyte vs ProxyRack) and obtain credentials before Phase 3 dev run
- Confirm the `dev` VPC/subnet networking allows Fargate outbound internet
