from typing import Callable

# Supabase (impostazione "Max rows" del progetto, 1000 di default) restituisce al massimo questo
# numero di righe per richiesta e tronca il resto senza segnalare nulla.
RIGHE_PER_RICHIESTA = 1000


def leggi_tutte(costruisci_query: Callable) -> list[dict]:
    """Legge TUTTE le righe di una SELECT, a blocchi da 1000.

    Senza questo, oltre le 1000 righe elenchi e somme risultano incompleti in silenzio (es. i costi
    della dashboard dimezzati con qualche centinaio di rapportini).

    `costruisci_query` deve restituire ogni volta una query nuova: `.range()` di postgrest aggiunge
    parametri invece di sostituirli, quindi la stessa query non si puo' riusare tra un blocco e
    l'altro. In coda all'ordinamento eventualmente gia' presente si aggiunge `id`, cosi' l'ordine e'
    stabile e nessuna riga viene saltata o letta due volte (es. righe inserite insieme hanno lo
    stesso created_at).
    """
    righe: list[dict] = []
    inizio = 0
    while True:
        blocco = (
            costruisci_query()
            .order("id")
            .range(inizio, inizio + RIGHE_PER_RICHIESTA - 1)
            .execute()
            .data
            or []
        )
        righe.extend(blocco)
        if len(blocco) < RIGHE_PER_RICHIESTA:
            return righe
        inizio += RIGHE_PER_RICHIESTA
