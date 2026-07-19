"""
Local CLI entry point for the Idealista web scraper (FEATURE-002, task 1.10).

Working invocation (matches the import root documented in
``scraper/__init__.py``, task 1.1)::

    cd src/etl && python -m data_collection.scraper --operation sale --output-dir data/s3/

or, from any directory::

    PYTHONPATH=src/etl python -m data_collection.scraper --operation sale --output-dir data/s3/

Run with no AWS credentials at all: passing ``--output-dir`` always
selects :class:`~repository.LocalListingRepository`, and the default
``PROXY_PROVIDER=none`` selects :class:`~proxies.NullProxyProvider` —
this is exactly the graph the CLI and the learning notebook (task 1.11)
use.

The ``SCRAPER_ENABLED`` kill switch (REVIEW-FEATURE-002 finding H2) is
checked *first*, before :func:`~factory.build_orchestrator` (and thus
the fetcher/proxy graph) is even constructed, so a disabled run never
touches the network.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Dict, List, Optional, Sequence

from .config import ScraperConfig
from .factory import build_orchestrator
from .repository import DEFAULT_OUTPUT_DIR
from .urls import OperationStrategy, RentStrategy, SaleStrategy

logger = logging.getLogger(__name__)

_STRATEGIES_BY_OPERATION: Dict[str, OperationStrategy] = {
    "sale": SaleStrategy(),
    "rent": RentStrategy(),
}


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI's argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m data_collection.scraper",
        description=(
            "Idealista web scraper — local run (NullProxyProvider + "
            "LocalListingRepository by default)."
        ),
    )
    parser.add_argument(
        "--operation",
        choices=["sale", "rent", "both"],
        default="both",
        help="Which operation(s) to scrape (default: both).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Local directory to write JSON pages to (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional cap on pages scraped per operation (default: unlimited).",
    )
    return parser


def _strategies_for(operation: str) -> List[OperationStrategy]:
    """Resolve the ``--operation`` choice to one or two strategies."""
    if operation == "both":
        return [SaleStrategy(), RentStrategy()]
    return [_STRATEGIES_BY_OPERATION[operation]]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``);
            mainly a test seam.

    Returns:
        Process exit code (``0`` on success, including the kill-switch
        short-circuit).
    """
    logging.basicConfig(level=logging.INFO)
    args = _build_arg_parser().parse_args(argv)

    config = ScraperConfig.from_env(dict(os.environ))

    # Kill switch: checked before anything else is built (no fetcher,
    # no proxy provider, no network) — REVIEW-FEATURE-002 finding H2.
    if not config.scraper_enabled:
        logger.info(
            "SCRAPER_ENABLED=false — kill switch engaged, exiting without scraping"
        )
        return 0

    orchestrator = build_orchestrator(config, output_dir=args.output_dir)

    for strategy in _strategies_for(args.operation):
        logger.info("Scraping operation=%s", strategy.operation_label)
        orchestrator.scrape(strategy, max_pages=args.max_pages)

    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via main()
    sys.exit(main())
