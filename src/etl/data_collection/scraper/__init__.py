"""
Idealista web-scraper OOP core (FEATURE-002, task 1.1).

This package is a self-contained, testable scraper for Idealista search
results that supplements the API collector (``bronze_collector.py``),
which is capped at 100 listings/month. It is deliberately isolated from
the existing bronze pipeline: no shared imports, no shared S3 prefix.

Import root (resolves REVIEW-FEATURE-002 finding M2)
------------------------------------------------------
The existing ``data_collection`` modules (e.g. ``bronze_collector.py``)
are flat top-level modules imported without any ``etl.`` namespace,
because the test suite and CI both run with ``cwd=src/etl`` (see
``.github/workflows/python-test.yml``: ``cd src/etl && pytest
data_collection/tests/ ...``). There is no ``src/etl/__init__.py`` or
``src/etl/data_collection/__init__.py`` chain, so ``etl.data_collection``
is not an importable namespace today.

This package follows the same convention: it is importable as
``data_collection.scraper`` (not ``etl.data_collection.scraper``) from
any process whose working directory (or ``PYTHONPATH``) includes
``src/etl``. Concretely:

* **pytest / CI** — already ``cd src/etl`` before invoking pytest, so
  ``import data_collection.scraper`` resolves without extra config.
* **Local CLI** — run from ``src/etl``::

      cd src/etl && python -m data_collection.scraper --help

  or, from any other directory, with ``PYTHONPATH`` pointed at
  ``src/etl``::

      PYTHONPATH=src/etl python -m data_collection.scraper --help

* **Docker (Phase 2, out of scope here)** — the container ``WORKDIR``
  will be set to the equivalent of ``src/etl`` so ``CMD`` resolves
  identically.

``data_collection`` has no ``__init__.py`` of its own; Python 3's
implicit namespace packages (PEP 420) make ``data_collection.scraper``
importable as long as ``scraper/`` itself (this package) declares
``__init__.py``, which it does.
"""

from __future__ import annotations
