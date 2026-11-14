"""
Secrets edge interface + adapters for the ETL pipeline.

Defines the :class:`SecretsProvider` Protocol (Abstraction / Dependency
Inversion), the boto3-backed production adapters
:class:`SecretsManagerProvider` and :class:`SsmParameterProvider` (Adapter
pattern) and the :class:`InMemorySecretsProvider` test fake (Polymorphism —
all interchangeable behind the same Protocol).
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class SecretError(Exception):
    """Raised when a secret cannot be retrieved or parsed."""


@runtime_checkable
class SecretsProvider(Protocol):
    """
    Narrow secrets-retrieval interface (Interface Segregation).

    A single read method — consumers never create, rotate or delete
    secrets, so the Protocol must not offer those operations.
    """

    def get_secret(self, name: str) -> Dict[str, str]:
        """Return the decoded key/value mapping stored under *name*."""
        ...


class SecretsManagerProvider:
    """
    boto3-backed :class:`SecretsProvider` adapter for AWS Secrets Manager.

    Adapter pattern: wraps the vendor SDK behind the project-owned
    Protocol. Encapsulation: the client is private; callers only see
    :meth:`get_secret`.
    """

    def __init__(self, secrets_client: object | None = None) -> None:
        """
        Args:
            secrets_client: Optional pre-built boto3 Secrets Manager
                client (injected in tests via moto). Created lazily from
                boto3 when omitted.
        """
        # boto3 stays inside the adapter (Dependency Inversion).
        import boto3

        self._client = (
            secrets_client
            if secrets_client is not None
            else boto3.client("secretsmanager")
        )

    def get_secret(self, name: str) -> Dict[str, str]:
        """
        Fetch and JSON-decode the secret string stored under *name*.

        Args:
            name: Secrets Manager secret name or ARN.

        Returns:
            The secret's key/value mapping.

        Raises:
            SecretError: When retrieval or JSON decoding fails.
        """
        import botocore.exceptions

        try:
            response = self._client.get_secret_value(SecretId=name)  # type: ignore[attr-defined]
            secret: Dict[str, str] = json.loads(response["SecretString"])
            return secret
        except botocore.exceptions.ClientError as exc:
            logger.error("Error retrieving secret %s: %s", name, exc)
            raise SecretError(f"Failed to retrieve credentials: {exc}") from exc
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Malformed secret payload for %s: %s", name, exc)
            raise SecretError(f"Malformed secret payload: {exc}") from exc


class SsmParameterProvider:
    """
    boto3-backed :class:`SecretsProvider` adapter for SSM Parameter Store.

    Adapter pattern: wraps the vendor SDK behind the project-owned
    Protocol. Each credential set is one ``SecureString`` parameter whose
    value is a JSON object, so the contract matches
    :class:`SecretsManagerProvider` (Liskov Substitution). Encapsulation:
    the client is private; callers only see :meth:`get_secret`.
    """

    def __init__(self, ssm_client: object | None = None) -> None:
        """
        Args:
            ssm_client: Optional pre-built boto3 SSM client (injected in
                tests via moto). Created lazily from boto3 when omitted.
        """
        # boto3 stays inside the adapter (Dependency Inversion).
        import boto3

        self._client = ssm_client if ssm_client is not None else boto3.client("ssm")

    def get_secret(self, name: str) -> Dict[str, str]:
        """
        Fetch, decrypt and JSON-decode the parameter stored under *name*.

        Args:
            name: Parameter name (starts with ``/``) or ARN.

        Returns:
            The parameter's key/value mapping.

        Raises:
            SecretError: When retrieval fails or the value is not a JSON
                object.
        """
        import botocore.exceptions

        try:
            # CRITICAL: without WithDecryption a SecureString comes back as
            # ciphertext, which would fail JSON decoding below.
            response = self._client.get_parameter(  # type: ignore[attr-defined]
                Name=name, WithDecryption=True
            )
            secret = json.loads(response["Parameter"]["Value"])
        except botocore.exceptions.ClientError as exc:
            logger.error("Error retrieving parameter %s: %s", name, exc)
            raise SecretError(f"Failed to retrieve credentials: {exc}") from exc
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Malformed secret payload for %s: %s", name, exc)
            raise SecretError(f"Malformed secret payload: {exc}") from exc

        if not isinstance(secret, dict):
            # Never log the value itself — only its type.
            logger.error("Parameter %s is not a JSON object", name)
            raise SecretError(
                f"Malformed secret payload: expected a JSON object, "
                f"got {type(secret).__name__}"
            )
        return secret


class InMemorySecretsProvider:
    """
    Dict-backed :class:`SecretsProvider` fake for unit tests.

    Polymorphism: interchangeable with :class:`SecretsManagerProvider`
    behind the shared Protocol.
    """

    def __init__(self, secrets: Dict[str, Dict[str, str]] | None = None) -> None:
        """
        Args:
            secrets: Mapping of secret name → key/value payload.
        """
        self._secrets: Dict[str, Dict[str, str]] = dict(secrets or {})

    def get_secret(self, name: str) -> Dict[str, str]:
        """
        Return the stored payload for *name*.

        Raises:
            SecretError: When no secret was registered under *name*,
                mirroring the production adapter's failure mode.
        """
        try:
            return self._secrets[name]
        except KeyError as exc:
            raise SecretError(f"Failed to retrieve credentials: {name}") from exc
