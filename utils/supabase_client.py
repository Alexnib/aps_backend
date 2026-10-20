import os
import queue
import threading
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials in .env file")

_local = threading.local()

# Client gia' creati e non in uso. Ogni thread ha il suo client (mai condiviso tra due thread nello
# stesso momento), ma i thread del server vengono chiusi dopo pochi secondi di inattivita': senza
# questo deposito ogni thread nuovo ricreava il client da zero (~0,3 s) e riapriva le connessioni
# sicure con Supabase (~0,5 s), e chi apriva una pagina dopo una pausa aspettava 1-5 secondi.
_client_liberi: "queue.SimpleQueue[Client]" = queue.SimpleQueue()


class _Prestito:
    """Client assegnato a un thread. Quando il thread termina, i suoi dati thread-local vengono
    eliminati e il client torna nel deposito per il thread successivo."""

    def __init__(self, client: Client):
        self.client = client

    def __del__(self):
        # SimpleQueue.put e' sicura anche da __del__ (e' rientrante).
        _client_liberi.put(self.client)


class SupabaseProxy:
    @property
    def _client(self) -> Client:
        prestito = getattr(_local, "prestito", None)
        if prestito is None:
            try:
                client = _client_liberi.get_nowait()
            except queue.Empty:
                client = create_client(SUPABASE_URL, SUPABASE_KEY)
            prestito = _local.prestito = _Prestito(client)
        return prestito.client

    def __getattr__(self, name):
        return getattr(self._client, name)

supabase: Client = SupabaseProxy()  # type: ignore
