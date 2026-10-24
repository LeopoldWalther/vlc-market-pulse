"""
Lambda entry point for the bronze Idealista collector (thin handler).

FEATURE-008 OOP refactor: all collection logic lives in
:mod:`bronze_collector`; this module only

1. wires up the production collaborators (**Factory** —
   :func:`build_collector`), and
2. translates the :class:`~bronze_collector.CollectionResult` into the
   Lambda response body (**Single Responsibility**).

The response body keeps the exact fields FEATURE-007's ExtractSummary
state parses: ``message``, ``timestamp``, ``sale_pages``, ``rent_pages``,
``duration_seconds``, ``total_size_mb``.
"""

import json
import logging
import os
from typing import Any, Dict

from bronze_collector import (  # noqa: F401 — re-exported for compatibility
    BronzeCollector,
    IdealistaAPIError,
    IdealistaApiClient,
    SearchConfig,
)
from common.metrics_publisher import CloudWatchMetricsPublisher
from common.object_store import S3ObjectStore
from common.secrets_provider import SecretsManagerProvider

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def build_collector(env: Dict[str, str]) -> BronzeCollector:
    """
    Factory: construct a fully wired production BronzeCollector.

    All AWS adapters are instantiated here — at the composition edge —
    and injected into the collector (Dependency Inversion). Tests build
    the collector directly with in-memory fakes instead.

    Args:
        env: Environment mapping (normally ``os.environ``) providing
            S3_BUCKET, SECRET_NAME_LVW, SECRET_NAME_PMV
            and optionally S3_PREFIX.

    Returns:
        A ready-to-run collector.

    Raises:
        IdealistaAPIError: If a required environment variable is missing.
    """
    s3_bucket = env.get("S3_BUCKET")
    secret_name_lvw = env.get("SECRET_NAME_LVW")
    secret_name_pmv = env.get("SECRET_NAME_PMV")

    if not all([s3_bucket, secret_name_lvw, secret_name_pmv]):
        raise IdealistaAPIError(
            "Missing required environment variables: "
            "S3_BUCKET, SECRET_NAME_LVW, SECRET_NAME_PMV"
        )

    # Narrowed by the guard above; assertions keep mypy precise.
    assert s3_bucket is not None
    assert secret_name_lvw is not None
    assert secret_name_pmv is not None

    # Medallion Architecture: bronze layer prefix.
    s3_prefix = env.get("S3_PREFIX", "bronze/idealista/")

    return BronzeCollector(
        object_store=S3ObjectStore(bucket=s3_bucket),
        secrets_provider=SecretsManagerProvider(),
        api_client=IdealistaApiClient(),
        secret_name_lvw=secret_name_lvw,
        secret_name_pmv=secret_name_pmv,
        s3_prefix=s3_prefix,
        # FEATURE-012 (task 12.3): publish per-attempt quota metrics so
        # the pipeline-health Lambda can evaluate API-quota pressure.
        metrics_publisher=CloudWatchMetricsPublisher(),
    )


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler: build the collector, run it, shape the response.

    Args:
        event: Lambda event object (supports ``'test_mode': true`` to
            limit collection to 1 page per operation).
        context: Lambda context object (unused).

    Returns:
        Response dictionary with status code and JSON body.
    """
    try:
        test_mode = event.get("test_mode", False) if isinstance(event, dict) else False

        collector = build_collector(dict(os.environ))
        result = collector.collect(test_mode=test_mode)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": result.message,
                    "timestamp": result.timestamp,
                    "sale_pages": result.sale_pages,
                    "rent_pages": result.rent_pages,
                    "duration_seconds": result.duration_seconds,
                    "total_size_mb": round(result.total_size_mb, 2),
                }
            ),
        }

    except IdealistaAPIError as e:
        logger.error(f"Idealista API error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Unexpected error: {str(e)}"}),
        }
