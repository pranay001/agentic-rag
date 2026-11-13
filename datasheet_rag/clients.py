"""Process-wide OpenAI and Qdrant clients (both are thread-safe for our usage)."""
from openai import OpenAI
from qdrant_client import QdrantClient

from . import config

oai = OpenAI(max_retries=5, timeout=300)
qdrant = QdrantClient(url=config.QDRANT_URL, timeout=120)
