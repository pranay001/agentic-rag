"""Settings shared by all notebooks. Values come from .env / environment with sensible local defaults."""
import os

import truststore

# Use the OS certificate store for HTTPS (corporate TLS inspection breaks certifi-only verification)
truststore.inject_into_ssl()

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "datasheets")

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIM = 3072

PHOENIX_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
PHOENIX_PROJECT = os.getenv("PHOENIX_PROJECT_NAME", "datasheet-rag")

if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY missing - add it to .env")
