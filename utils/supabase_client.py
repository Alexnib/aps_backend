import os
import threading
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials in .env file")

_local = threading.local()

class SupabaseProxy:
    @property
    def _client(self) -> Client:
        if not hasattr(_local, "client"):
            _local.client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _local.client

    def __getattr__(self, name):
        return getattr(self._client, name)

supabase: Client = SupabaseProxy()  # type: ignore
