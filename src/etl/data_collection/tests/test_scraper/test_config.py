"""
Unit tests for ScraperConfig (FEATURE-002, task 1.10).
"""

from __future__ import annotations

from data_collection.scraper.config import ScraperConfig


class TestScraperConfigDefaults:
    def test_defaults_with_empty_env(self) -> None:
        config = ScraperConfig.from_env({})

        assert config.s3_bucket is None
        assert config.s3_prefix == "bronze/idealista-scraper/"
        assert config.sns_topic_arn is None
        assert config.proxy_provider == "none"
        assert config.proxy_secret_name is None
        assert config.aws_region == "eu-central-1"
        assert config.scraper_enabled is True

    def test_reads_all_fields_from_env(self) -> None:
        env = {
            "S3_BUCKET": "my-bucket",
            "S3_PREFIX": "custom/prefix/",
            "SNS_TOPIC_ARN": "arn:aws:sns:eu-central-1:123:topic",
            "PROXY_PROVIDER": "rayobyte",
            "PROXY_SECRET_NAME": "scraper/proxy-creds",
            "AWS_REGION": "us-east-1",
            "SCRAPER_ENABLED": "true",
        }
        config = ScraperConfig.from_env(env)

        assert config.s3_bucket == "my-bucket"
        assert config.s3_prefix == "custom/prefix/"
        assert config.sns_topic_arn == "arn:aws:sns:eu-central-1:123:topic"
        assert config.proxy_provider == "rayobyte"
        assert config.proxy_secret_name == "scraper/proxy-creds"
        assert config.aws_region == "us-east-1"
        assert config.scraper_enabled is True


class TestKillSwitchParsing:
    def test_default_is_enabled(self) -> None:
        assert ScraperConfig.from_env({}).scraper_enabled is True

    def test_explicit_true_is_enabled(self) -> None:
        assert (
            ScraperConfig.from_env({"SCRAPER_ENABLED": "true"}).scraper_enabled is True
        )

    def test_false_string_disables(self) -> None:
        assert (
            ScraperConfig.from_env({"SCRAPER_ENABLED": "false"}).scraper_enabled
            is False
        )

    def test_zero_string_disables(self) -> None:
        assert ScraperConfig.from_env({"SCRAPER_ENABLED": "0"}).scraper_enabled is False

    def test_case_insensitive_false(self) -> None:
        assert (
            ScraperConfig.from_env({"SCRAPER_ENABLED": "FALSE"}).scraper_enabled
            is False
        )

    def test_no_string_disables(self) -> None:
        assert (
            ScraperConfig.from_env({"SCRAPER_ENABLED": "no"}).scraper_enabled is False
        )


class TestScraperConfigImmutability:
    def test_config_is_frozen(self) -> None:
        config = ScraperConfig.from_env({})
        try:
            config.s3_bucket = "changed"  # type: ignore[misc]
            raised = False
        except Exception:
            raised = True
        assert raised
