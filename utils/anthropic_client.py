import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(override=True)

_client: Anthropic | None = None


def get_anthropic_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY non configurata nel file .env")
        _client = Anthropic(api_key=api_key, timeout=600.0)
    return _client
