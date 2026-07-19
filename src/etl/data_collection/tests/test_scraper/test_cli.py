"""
Unit tests for the CLI, kill switch, and factory composition root (FEATURE-002, task 1.10).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from data_collection.scraper import __main__ as cli
from data_collection.scraper.config import ScraperConfig
from data_collection.scraper.errors import ScraperError
from data_collection.scraper.factory import build_orchestrator
from data_collection.scraper.orchestrator import ScrapeOrchestrator
from data_collection.scraper.proxies import NullProxyProvider
from data_collection.scraper.repository import LocalListingRepository


class TestFactoryLocalGraph:
    """The local (no-AWS) composition path used by the CLI/notebook."""

    def test_builds_orchestrator_with_local_repository_and_null_proxy(
        self, tmp_path: Path
    ) -> None:
        config = ScraperConfig.from_env({})
        orchestrator = build_orchestrator(config, output_dir=str(tmp_path))

        assert isinstance(orchestrator, ScrapeOrchestrator)
        assert isinstance(
            orchestrator._repository, LocalListingRepository
        )  # noqa: SLF001
        assert isinstance(
            orchestrator._proxy_provider, NullProxyProvider
        )  # noqa: SLF001

    def test_missing_s3_bucket_without_output_dir_raises(self) -> None:
        config = ScraperConfig.from_env({})
        with pytest.raises(ScraperError):
            build_orchestrator(config, output_dir=None)

    def test_paid_proxy_provider_without_secret_name_raises(
        self, tmp_path: Path
    ) -> None:
        config = ScraperConfig.from_env({"PROXY_PROVIDER": "rayobyte"})
        with pytest.raises(ScraperError):
            build_orchestrator(config, output_dir=str(tmp_path))


class TestCliArgumentParsing:
    def test_help_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli._build_arg_parser().parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_defaults(self) -> None:
        args = cli._build_arg_parser().parse_args([])
        assert args.operation == "both"
        assert args.max_pages is None

    def test_operation_and_max_pages_parsed(self) -> None:
        args = cli._build_arg_parser().parse_args(
            ["--operation", "sale", "--max-pages", "2"]
        )
        assert args.operation == "sale"
        assert args.max_pages == 2


class TestKillSwitch:
    """SCRAPER_ENABLED=false must short-circuit before any graph is built."""

    def test_disabled_short_circuits_without_building_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCRAPER_ENABLED", "false")

        with patch("data_collection.scraper.__main__.build_orchestrator") as mock_build:
            exit_code = cli.main(["--operation", "sale"])

        assert exit_code == 0
        mock_build.assert_not_called()

    def test_enabled_builds_and_scrapes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SCRAPER_ENABLED", "true")
        monkeypatch.delenv("S3_BUCKET", raising=False)

        fake_orchestrator = MagicMock()
        with patch(
            "data_collection.scraper.__main__.build_orchestrator",
            return_value=fake_orchestrator,
        ) as mock_build:
            exit_code = cli.main(
                [
                    "--operation",
                    "sale",
                    "--output-dir",
                    str(tmp_path),
                    "--max-pages",
                    "1",
                ]
            )

        assert exit_code == 0
        mock_build.assert_called_once()
        fake_orchestrator.scrape.assert_called_once()
        _, kwargs = fake_orchestrator.scrape.call_args
        assert kwargs["max_pages"] == 1

    def test_both_operations_scrape_sale_and_rent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SCRAPER_ENABLED", "true")

        fake_orchestrator = MagicMock()
        with patch(
            "data_collection.scraper.__main__.build_orchestrator",
            return_value=fake_orchestrator,
        ):
            cli.main(["--operation", "both", "--output-dir", str(tmp_path)])

        assert fake_orchestrator.scrape.call_count == 2
        scraped_labels = {
            call.args[0].operation_label
            for call in fake_orchestrator.scrape.call_args_list
        }
        assert scraped_labels == {"sale", "rent"}


class TestCliLocalSmoke:
    """A fully-mocked local run (no real HTTP) exercises the whole CLI path."""

    def test_local_run_with_null_proxy_completes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SCRAPER_ENABLED", "true")
        monkeypatch.delenv("S3_BUCKET", raising=False)
        monkeypatch.delenv("PROXY_PROVIDER", raising=False)

        with patch(
            "data_collection.scraper.fetcher.CloudscraperFetcher._transport"
        ) as mock_transport:
            mock_transport.return_value = type(
                "Resp", (), {"status_code": 200, "text": "<html></html>"}
            )()
            exit_code = cli.main(
                [
                    "--operation",
                    "sale",
                    "--output-dir",
                    str(tmp_path),
                    "--max-pages",
                    "1",
                ]
            )

        assert exit_code == 0
