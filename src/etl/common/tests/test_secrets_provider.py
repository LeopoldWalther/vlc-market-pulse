"""
Contract tests for the SecretsProvider Protocol and its implementations.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Generator

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from common.secrets_provider import (  # noqa: E402
    InMemorySecretsProvider,
    SecretError,
    SecretsManagerProvider,
    SecretsProvider,
    SsmParameterProvider,
)

SECRET_NAME = "dev/idealista/lvw"
SECRET_PAYLOAD = {"api_key": "key-123", "api_secret": "secret-456"}
PARAMETER_NAME = "/vlc-market-pulse/dev/idealista/lvw-api-credentials"


@pytest.fixture()
def sm_provider() -> Generator[SecretsManagerProvider, None, None]:
    """Yield a SecretsManagerProvider backed by a moto-mocked secret."""
    with mock_aws():
        client = boto3.client("secretsmanager", region_name="eu-central-1")
        client.create_secret(Name=SECRET_NAME, SecretString=json.dumps(SECRET_PAYLOAD))
        yield SecretsManagerProvider(secrets_client=client)


def _ssm_provider_with(name: str, value: str) -> SsmParameterProvider:
    """Return an SsmParameterProvider whose mocked store holds one SecureString."""
    client = boto3.client("ssm", region_name="eu-central-1")
    client.put_parameter(Name=name, Value=value, Type="SecureString")
    return SsmParameterProvider(ssm_client=client)


@pytest.fixture()
def ssm_provider() -> Generator[SsmParameterProvider, None, None]:
    """Yield an SsmParameterProvider backed by a moto-mocked SecureString."""
    with mock_aws():
        yield _ssm_provider_with(PARAMETER_NAME, json.dumps(SECRET_PAYLOAD))


class TestProtocolConformance:
    """Both implementations must satisfy the runtime-checkable Protocol."""

    def test_in_memory_provider_satisfies_protocol(self) -> None:
        assert isinstance(InMemorySecretsProvider(), SecretsProvider)

    def test_secrets_manager_provider_satisfies_protocol(
        self, sm_provider: SecretsManagerProvider
    ) -> None:
        assert isinstance(sm_provider, SecretsProvider)

    def test_ssm_parameter_provider_satisfies_protocol(
        self, ssm_provider: SsmParameterProvider
    ) -> None:
        assert isinstance(ssm_provider, SecretsProvider)


class TestInMemorySecretsProvider:
    """Behavioural contract of the dict-backed fake."""

    def test_returns_registered_payload(self) -> None:
        provider = InMemorySecretsProvider({SECRET_NAME: SECRET_PAYLOAD})

        assert provider.get_secret(SECRET_NAME) == SECRET_PAYLOAD

    def test_missing_secret_raises_secret_error(self) -> None:
        provider = InMemorySecretsProvider()

        with pytest.raises(SecretError, match="Failed to retrieve credentials"):
            provider.get_secret("unknown")


class TestSecretsManagerProvider:
    """Behavioural contract of the boto3 adapter under moto."""

    def test_returns_decoded_payload(self, sm_provider: SecretsManagerProvider) -> None:
        assert sm_provider.get_secret(SECRET_NAME) == SECRET_PAYLOAD

    def test_missing_secret_raises_secret_error(
        self, sm_provider: SecretsManagerProvider
    ) -> None:
        with pytest.raises(SecretError, match="Failed to retrieve credentials"):
            sm_provider.get_secret("does/not/exist")

    def test_malformed_json_raises_secret_error(self) -> None:
        with mock_aws():
            client = boto3.client("secretsmanager", region_name="eu-central-1")
            client.create_secret(Name="broken", SecretString="not-json")
            provider = SecretsManagerProvider(secrets_client=client)

            with pytest.raises(SecretError, match="Malformed secret payload"):
                provider.get_secret("broken")


class TestSsmParameterProvider:
    """Behavioural contract of the Parameter Store adapter under moto."""

    def test_returns_decoded_payload(self, ssm_provider: SsmParameterProvider) -> None:
        assert ssm_provider.get_secret(PARAMETER_NAME) == SECRET_PAYLOAD

    def test_requests_decryption(self) -> None:
        """A SecureString read without decryption returns ciphertext, not JSON."""

        class RecordingClient:
            """Minimal SSM client double that records the call arguments."""

            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def get_parameter(self, **kwargs: object) -> dict[str, dict[str, str]]:
                self.calls.append(kwargs)
                return {"Parameter": {"Value": json.dumps(SECRET_PAYLOAD)}}

        client = RecordingClient()

        SsmParameterProvider(ssm_client=client).get_secret(PARAMETER_NAME)

        assert client.calls == [{"Name": PARAMETER_NAME, "WithDecryption": True}]

    def test_missing_parameter_raises_secret_error(
        self, ssm_provider: SsmParameterProvider
    ) -> None:
        with pytest.raises(SecretError, match="Failed to retrieve credentials"):
            ssm_provider.get_secret("/does/not/exist")

    def test_malformed_json_raises_secret_error(self) -> None:
        with mock_aws():
            provider = _ssm_provider_with("/broken", "not-json")

            with pytest.raises(SecretError, match="Malformed secret payload"):
                provider.get_secret("/broken")

    def test_non_object_payload_raises_secret_error(self) -> None:
        with mock_aws():
            provider = _ssm_provider_with("/a-list", json.dumps(["key", "secret"]))

            with pytest.raises(SecretError, match="Malformed secret payload"):
                provider.get_secret("/a-list")
