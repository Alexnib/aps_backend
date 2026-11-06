"""Elenchi paginati con ricerca, condivisi dalle pagine dell'app (cantieri, operai, mezzi, ...).

Ogni elenco restituisce una "pagina" di righe piu' il numero totale di righe che corrispondono ai
filtri, cosi' il frontend carica 50 righe alla volta mentre si scorre (come i rapportini) invece
di scaricare tutto l'archivio a ogni apertura della pagina.
"""
import re
from typing import Optional

# Righe per pagina: di default 50, mai oltre 200 per richiesta.
PER_PAGINA = 50
PER_PAGINA_MAX = 200

# Oltre questo numero di parole la ricerca non diventa piu' precisa, solo piu' lenta.
MAX_PAROLE = 8


def parole_ricerca(q: Optional[str]) -> list[str]:
    """Parole del testo cercato, senza spazi superflui. `*` e' il jolly di PostgREST: si toglie."""
    if not q:
        return []
    return [p for p in re.split(r"\s+", q.replace("*", " ").strip()) if p][:MAX_PAROLE]


def _modello_ilike(parola: str) -> str:
    """Valore per un filtro `ilike` che cerca la parola alla lettera, ovunque nel testo.

    Due livelli di escape: prima i caratteri jolly di ILIKE (% e _), poi la sintassi delle liste
    di PostgREST, dove il valore va tra virgolette perche' virgole e parentesi (frequenti nelle
    descrizioni dei materiali, es. 3/4"X1") altrimenti spezzerebbero il filtro.
    """
    letterale = parola.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    quotato = letterale.replace("\\", "\\\\").replace('"', '\\"')
    return f'"*{quotato}*"'


def filtra_ricerca(query, q: Optional[str], colonne: list[str]):
    """Ogni parola cercata deve comparire in almeno una delle colonne (maiuscole indifferenti).

    "mario rossi" trova l'operaio con nome Mario e cognome Rossi: le parole sono in AND, le
    colonne in OR. Ogni `.or_()` e' un filtro a se', e PostgREST mette i filtri in AND.
    """
    for parola in parole_ricerca(q):
        modello = _modello_ilike(parola)
        query = query.or_(",".join(f"{colonna}.ilike.{modello}" for colonna in colonne))
    return query


def leggi_pagina(query, offset: int, limit: int) -> dict:
    """Esegue una SELECT fatta con `count="exact"` e gia' ordinata, restituendo una pagina.

    In coda all'ordinamento si aggiunge `id`: con valori uguali (es. due fornitori con lo stesso
    nome) l'ordine resta stabile tra una pagina e l'altra e nessuna riga si ripete o si perde.
    """
    res = query.order("id").range(offset, offset + limit - 1).execute()
    return {"elementi": res.data or [], "totale": res.count or 0, "offset": offset, "limit": limit}
