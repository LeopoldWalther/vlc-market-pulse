"""
Shared edge interfaces for the VLC real-estate ETL Lambdas.

This package defines the three narrow Protocols that separate the pure
pipeline logic from AWS (Dependency Inversion), together with their
production adapters (Adapter pattern around boto3) and in-memory fakes
for tests (Polymorphism — fakes and adapters are interchangeable).

boto3 is imported ONLY inside the adapter modules; core pipeline code
depends exclusively on the Protocols.
"""

from common.notifier import InMemoryNotifier, Notifier, SnsNotifier
from common.object_store import InMemoryObjectStore, ObjectStore, S3ObjectStore
from common.secrets_provider import (
    InMemorySecretsProvider,
    SecretsManagerProvider,
    SecretsProvider,
    SsmParameterProvider,
)

__all__ = [
    "InMemoryNotifier",
    "InMemoryObjectStore",
    "InMemorySecretsProvider",
    "Notifier",
    "ObjectStore",
    "S3ObjectStore",
    "SecretsManagerProvider",
    "SecretsProvider",
    "SnsNotifier",
    "SsmParameterProvider",
]
