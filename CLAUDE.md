# VLC Market Pulse — Project Instructions

## Project Overview

**VLC Real Estate Analytics** is an automated real estate data collection and analysis platform for Valencia, Spain. It solves the problem of tracking property market trends over time by collecting weekly snapshots of sale and rental listings from the Idealista API, storing them in AWS S3, and enabling downstream analysis via Jupyter notebooks.

### Key Features

- **Automated Data Collection** — Weekly Lambda execution via EventBridge (Sundays 12:00 UTC)
- **Dual Listing Types** — Both sale and rental property data from the Idealista Property Search API v3.5
- **Historical Time-Series** — Append-only JSON storage in S3 for long-term market trend analysis
- **Medallion Architecture** — Raw API responses land in a bronze layer (`bronze/idealista/` S3 prefix)
- **Multi-Environment** — Separate `dev` and `prod` Terraform-managed environments
- **Secure Credential Management** — Two API credential sets (LVW + PMV) stored in AWS Secrets Manager
- **Serverless & Cost-Efficient** — Estimated < $5/month across both environments

### Search Parameters

- **Location**: Valencia city center (39.4693441, −0.379561), 1500 m radius
- **Property Type**: Homes, 100–160 m², elevator, good preservation
- **Operations**: `sale` and `rent`

## Architecture

### High-Level Data Flow

```
┌──────────────────┐
│   EventBridge    │  cron(0 12 ? * SUN *)  — weekly on Sundays
└────────┬─────────┘
         │
         ▼
┌──────────────────┐       ┌───────────────────────┐
│  Lambda Function │──────▶│  AWS Secrets Manager  │
│  Python 3.12     │       │  (LVW + PMV API creds) │
│  256 MB / 900 s  │       └───────────────────────┘
└────────┬─────────┘
         │  OAuth2 token request
         ▼
┌──────────────────┐
│  Idealista API   │  Property Search API v3.5
│  (sale + rent)   │  Paginated JSON responses
└────────┬─────────┘
         │  PutObject
         ▼
┌──────────────────┐       ┌───────────────────────┐
│  S3 Bucket       │       │  SNS Topic            │
│  bronze/idealista│       │  (alerts on failure)  │
│  /{date}/{file}  │       └───────────────────────┘
└──────────────────┘
         │
         ▼
┌──────────────────┐
│  Jupyter         │  valenciaRealEstatePriceAnalysis.ipynb
│  Notebooks       │  Local or S3-backed analysis
└──────────────────┘
```

### Infrastructure Layout

```
infrastructure/
├── bootstrap/          # Remote state S3 bucket + lock
├── modules/
│   ├── lambda/         # Lambda function, IAM role/policies, EventBridge rule, CW log group (bronze)
│   ├── s3/             # S3 listings bucket (AES-256 encryption)
│   ├── secrets/        # Secrets Manager secrets for API credentials
│   └── sns/            # SNS topic for error alerting
└── environments/
    ├── dev/            # Development environment (terraform.tfvars + secrets.tfvars)
    └── prod/           # Production environment
```

### Source Code Layout

```
src/
├── etl/
│   ├── data_collection/
│   │   ├── idealista_listings_collector.py  # Lambda handler — main entrypoint
│   │   ├── requirements.txt                 # Runtime deps (requests, boto3)
│   │   └── tests/                           # Unit + integration tests
│   ├── data_processing/                     # Silver/gold layer (future)
│   ├── lambda_layers/                       # Requests library as Lambda Layer
│   └── requirements-dev.txt                 # Dev deps (pytest, moto, black, ruff, mypy)
└── notebooks/
    └── valenciaRealEstatePriceAnalysis.ipynb
```

### Design Patterns & OOP

The codebase is designed around the **four pillars of OOP** (encapsulation, abstraction,
inheritance, polymorphism) and the **SOLID** principles. Design patterns are applied deliberately
where they remove duplication or coupling — never for their own sake. See
[Object-Oriented Design](#object-oriented-design--the-4-pillars) and
[SOLID Principles](#solid-principles) below for the standards new code must meet.

- **Strategy Pattern** — `SearchConfig` encapsulates API search parameters; swap configs without changing orchestration logic
- **Dependency Injection** — collaborators (object store, secrets provider, notifier) injected via constructors; real AWS implementations by default, fakes in tests
- **Adapter Pattern** — third-party SDKs (boto3, requests) wrapped behind project-owned interfaces so core logic stays vendor-agnostic
- **Template Method** — a base collector defines the fetch → parse → persist skeleton; subclasses fill in operation-specific steps
- **Single Responsibility** — each class/function has one reason to change; the Lambda handler stays thin and delegates to focused collaborators
- **Custom Exceptions** — `IdealistaAPIError` for domain-specific error handling and clean caller code

### CI/CD Pipelines (`.github/workflows/`)

| Workflow | Trigger | Purpose |
|---|---|---|
| `python-test.yml` | Push / PR | Run pytest, mypy, ruff, black |
| `terraform-validate.yml` | Push / PR | `terraform fmt -check` + `terraform validate` |
| `deploy-lambda.yml` | Merge to main | Package and deploy Lambda to AWS |

## Tech Stack

| Category | Technology | Details |
|---|---|---|
| **Language** | Python 3.12 | Type hints enforced via mypy; Lambda runtime |
| **Compute** | AWS Lambda | 256 MB memory, 900 s timeout, serverless |
| **Scheduling** | AWS EventBridge | `cron(0 12 ? * SUN *)` — weekly Sundays |
| **Storage** | AWS S3 | Bronze layer; AES-256 encryption at rest |
| **Secrets** | AWS Secrets Manager | Dual credential sets: LVW + PMV |
| **Alerting** | AWS SNS | Error notifications from Lambda |
| **Monitoring** | AWS CloudWatch Logs | 30-day retention; `/aws/lambda/{env}-idealista-collector` |
| **External API** | Idealista Property Search API v3.5 | OAuth2; paginated JSON responses |
| **IaC** | Terraform 1.14.3 | Providers: `hashicorp/aws >= 5.0, < 6.0`; S3 remote state |
| **Analysis** | Jupyter Notebooks | Local analysis; pandas, matplotlib |
| **Testing** | pytest 8.0.0 | Unit + integration; moto for AWS mocking |
| **Linting** | Ruff 0.1.13 | Fast Python linter |
| **Formatting** | Black 24.1.0 | Deterministic code formatting |
| **Type Checking** | mypy 1.8.0 | Strict type validation |
| **Pre-commit** | pre-commit hooks | black, ruff, mypy, check-yaml, check-json |

## Dependencies

### Runtime (`src/etl/data_collection/requirements.txt`)

```
requests>=2.31.0   # HTTP client for Idealista API calls
boto3>=1.34.0      # AWS SDK — S3, Secrets Manager, SNS
```

### Lambda Layer (`src/etl/lambda_layers/`)

- `requests` library packaged as a reusable Lambda Layer (Python 3.12 compatible)

### Development (`src/etl/requirements-dev.txt`)

```
# Testing
pytest==8.0.0
pytest-cov==4.1.0
pytest-mock==3.12.0
moto[s3,secretsmanager]==5.0.0  # AWS service mocking

# Code Quality
black==24.1.0
ruff==0.1.13
mypy==1.8.0
boto3-stubs[lambda,s3,secretsmanager]==1.34.0
types-requests==2.31.0
```

### Terraform (`infrastructure/`)

```hcl
terraform  = ">= 1.14.3"
hashicorp/aws    = ">= 5.0, < 6.0"
hashicorp/archive = ">= 2.0"
```

## Key Files

| File | Purpose |
|---|---|
| `src/etl/data_collection/idealista_listings_collector.py` | Main Lambda handler — OAuth2 auth, paginated API fetch, S3 write, SNS notify |
| `infrastructure/modules/lambda/main.tf` | Reusable Terraform module — Lambda, IAM, EventBridge, CloudWatch |
| `infrastructure/environments/dev/` | Dev environment Terraform root module |
| `infrastructure/environments/prod/` | Prod environment Terraform root module |
| `infrastructure/bootstrap/` | Remote state S3 + DynamoDB lock (one-time setup) |
| `src/etl/requirements-dev.txt` | All development/test dependencies |
| `src/notebooks/valenciaRealEstatePriceAnalysis.ipynb` | Main analysis notebook |
| `.pre-commit-config.yaml` | Pre-commit hooks scoped to `src/etl/` |
| `documentation/DATA_COLLECTION_LAYER.md` | Detailed data collection architecture docs |

## Development Workflows

### Clean Code Principles

**MANDATORY for all code contributions:**

1. **Type Annotations**
   - ALL function signatures must include type hints for parameters and return values
   - Use `typing` module for complex types: `Optional`, `List`, `Tuple`, `Dict`, `Union`
   - Example:
     ```python
     from typing import Optional, Tuple, List

     def process_data(items: List[str], default: Optional[str] = None) -> Tuple[List[str], int]:
         """Process items with optional default value."""
         processed = [item.strip() for item in items if item]
         return processed, len(processed)
     ```

2. **Comprehensive Docstrings**
   - Every class and public function MUST have a docstring
   - Follow Google/NumPy docstring style with:
     - Brief description (one line)
     - Detailed explanation if needed
     - Args section with types and descriptions
     - Returns section with type and description
     - Raises section if applicable
   - Example:
     ```python
     def validate_user_input(data: dict, required_fields: List[str]) -> dict:
         """
         Validate user input data against required fields.

         Args:
             data: User input dictionary with field name → value mappings
             required_fields: List of field names that must be present

         Returns:
             Validated data dictionary with all required fields present

         Raises:
             ValueError: If any required field is missing or invalid
         """
         # Implementation
     ```

3. **Strategic Code Comments**
   - Add comments for:
     - **Critical operations**: Explain WHY, not just WHAT
     - **Subtle/non-obvious logic**: e.g., edge case handling, algorithm choices
     - **Performance considerations**: Why specific implementation chosen
     - **Complex transformations**: Document data structure changes
   - Example:
     ```python
     # CRITICAL: Must validate before saving to prevent data corruption
     # Failed validations should rollback entire transaction
     if not validator.check_integrity(data):
         session.rollback()
         raise ValidationError("Data integrity check failed")

     # Performance: Using dict lookup O(1) instead of list scan O(n)
     user_map = {user.id: user for user in users}
     ```
   - **Avoid**: Obvious comments like `# increment counter` for `counter += 1`

4. **Code Organization**
   - Functions should be < 50 lines (split if longer)
   - Classes should have clear single responsibility
   - Extract magic numbers to named constants
   - Use meaningful variable names (avoid `x1`, `temp`, `data2`)

5. **Object-Oriented Design — the 4 Pillars**

   New non-trivial components SHOULD be modelled with classes that demonstrate the four pillars of
   OOP. Pure functions remain acceptable for small, stateless transformations, but anything that
   carries configuration, holds collaborators, or has multiple variants belongs in a class.

   - **Encapsulation** — keep state private (`_prefixed` attributes); expose intent through methods,
     not raw fields. A `SilverCleaner` owns its rules; callers ask it to `clean()`, they do not reach
     into its internals.
   - **Abstraction** — depend on small, focused interfaces (`typing.Protocol` or `abc.ABC`), not on
     concrete implementations. A handler that needs storage depends on an `ObjectStore` protocol, not
     on `boto3`'s S3 client directly.
   - **Inheritance** — share behaviour through a base class only when there is a genuine *is-a*
     relationship (e.g. `BronzeCollector(BaseCollector)`). Prefer composition over inheritance when in
     doubt; never inherit just to reuse code.
   - **Polymorphism** — let callers work against the abstraction so variants are interchangeable
     (e.g. a `LocalObjectStore` for tests and an `S3ObjectStore` in production satisfy the same
     protocol). No `isinstance` ladders or `if type == ...` branching.

6. **SOLID Principles**

   All object-oriented code MUST be reviewable against SOLID. Call out which principle a design serves.

   - **S — Single Responsibility** — one reason to change per class. Splitting fetch / transform /
     persist into separate collaborators is preferred over a god-object Lambda.
   - **O — Open/Closed** — add behaviour by adding a class, not by editing a `switch`. New aggregations
     plug in as new strategies.
   - **L — Liskov Substitution** — any subclass / protocol implementation must be usable wherever the
     base type is expected, with no surprising preconditions.
   - **I — Interface Segregation** — keep protocols narrow; a reader should not be forced to implement
     write methods it never uses.
   - **D — Dependency Inversion** — high-level orchestration depends on abstractions; concrete AWS
     clients are injected at the edges (constructor injection), never imported deep in the core logic.

7. **Design Patterns — apply deliberately, never for their own sake**

   Reach for a named pattern only when it removes real duplication or coupling, and name it in the
   docstring / plan so reviewers understand the intent. Patterns already endorsed in this codebase:

   - **Strategy** — `SearchConfig` / aggregation variants: encapsulate an interchangeable algorithm.
   - **Dependency Injection** — pass `ObjectStore`, `SecretsProvider`, `Notifier` into constructors;
     default to real AWS implementations, swap fakes in tests.
   - **Adapter** — wrap third-party SDKs (boto3, requests) behind project-owned interfaces so the core
     never speaks a vendor dialect.
   - **Template Method** — a `BaseCollector` defines the fetch → parse → persist skeleton; subclasses
     fill in the operation-specific steps.
   - **Factory** — centralise construction of wired-up objects (e.g. `build_collector(env)`), keeping
     `lambda_handler` thin.
   - **Custom Exceptions** — domain errors (`IdealistaAPIError`) over bare `Exception`.

   Avoid over-engineering: do not introduce a pattern, abstract base class, or extra layer for a
   one-off operation. The simplest design that honours SOLID wins.

8. **Coding Principles**
   - Major bugfixes and features should be developed in separate branches
   - Follow the established branch naming conventions
   - Commit messages should be clear and reference task IDs where applicable

### Testing Requirements

**ALL new features and bug fixes MUST include tests:**

1. **Unit Tests**
   - Location: `tests/unit/`
   - Test individual functions/classes in isolation
   - Use appropriate testing framework (pytest for Python, Jest for JavaScript, etc.)
   - Mock external dependencies (file I/O, network calls, databases)
   - Coverage target: >80% for new code
   - Example structure:
     ```python
     # tests/unit/test_validator.py
     import pytest
     from myproject.validator import DataValidator

     def test_validator_accepts_valid_data():
         """Test validator passes with valid input."""
         validator = DataValidator(required_fields=['name', 'email'])
         data = {'name': 'John', 'email': 'john@example.com'}

         result = validator.validate(data)

         assert result.is_valid
         assert len(result.errors) == 0

     def test_validator_rejects_missing_fields():
         """Test validator raises error for missing required fields."""
         validator = DataValidator(required_fields=['name', 'email'])
         data = {'name': 'John'}  # missing email

         with pytest.raises(ValueError, match="Missing required field: email"):
             validator.validate(data)
     ```

2. **Integration Tests**
   - Location: `tests/integration/`
   - Test component interactions (API + database, service layers)
   - Use small test datasets (fast execution)
   - Test end-to-end workflows
   - Example:
     ```python
     # tests/integration/test_user_service.py
     def test_create_and_retrieve_user():
         """Test complete user creation and retrieval workflow."""
         service = UserService(database=test_db)

         # Create user
         user_id = service.create_user(name="Alice", email="alice@test.com")

         # Retrieve user
         user = service.get_user(user_id)

         assert user.name == "Alice"
         assert user.email == "alice@test.com"
     ```

3. **Test Naming Convention**
   - Prefix all test functions with `test_`
   - Use descriptive names: `test_validator_handles_empty_string_gracefully`
   - Group related tests in classes: `class TestUserAuthentication:`

4. **Running Tests**
   ```bash
   # Run all tests
   pytest tests/  # Python
   npm test       # JavaScript/Node

   # Run with coverage
   pytest --cov=src tests/
   npm test -- --coverage

   # Run specific test file
   pytest tests/unit/test_validator.py -v
   ```

5. **Test Quality Standards**
   - Each test should test ONE thing
   - Tests must be deterministic (use fixed seeds, mock time/randomness)
   - Use fixtures for common setup
   - Include edge cases: empty inputs, boundary values, error conditions

**Before Committing:**
1. ✅ All functions have type hints
2. ✅ All public APIs have docstrings
3. ✅ Critical/subtle code has explanatory comments
4. ✅ Tests written and passing
5. ✅ Code follows project conventions
