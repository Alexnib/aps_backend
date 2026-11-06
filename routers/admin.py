"""Pannello Admin (backoffice): approvazione, blocco e visualizzazione degli account.

Il ruolo (admin / user) non si cambia dall'applicazione: gli admin si decidono nel database.

Ogni endpoint richiede un admin attivo (richiedi_admin) e agisce sempre con l'identita' reale
dell'admin, anche se nel browser e' in corso una visualizzazione di un altro account.
"""
import logging
import traceback
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from models.admin import StatoUtenteUpdate
from utils.auth_deps import Profilo, carica_profilo, dimentica_profilo, migrazione_mancante, richiedi_admin
from utils.elenchi import PER_PAGINA, PER_PAGINA_MAX, parole_ricerca
from utils.errors import raise_db_error
from utils.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

MIGRAZIONE_DA_ESEGUIRE = (
    "Il pannello Admin richiede la migrazione migrations/20261106_ruoli_e_approvazione.sql: "
    "eseguila nel SQL Editor di Supabase."
)

_SELECT_UTENTE = "id, nome, cognome, email, stato, created_at, company_id, role(codice), companies(partita_iva, indirizzo)"


def _riga_utente(riga: dict) -> dict:
    """Stessa forma delle righe dell'elenco (funzione SQL admin_utenti_elenco)."""
    azienda = riga.get("companies") or {}
    return {
        "id": riga["id"],
        "nome": riga["nome"],
        "cognome": riga["cognome"],
        "email": riga.get("email"),
        "stato": riga["stato"],
        "ruolo": (riga.get("role") or {}).get("codice") or "user",
        "created_at": riga.get("created_at"),
        "company_id": riga.get("company_id"),
        "partita_iva": azienda.get("partita_iva"),
        "indirizzo": azienda.get("indirizzo"),
    }


def _utente_o_404(utente_id: str) -> dict:
    try:
        uuid.UUID(utente_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    righe = supabase.table("users").select(_SELECT_UTENTE).eq("id", utente_id).execute().data
    if not righe:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    return _riga_utente(righe[0])


def _non_su_se_stessi(admin: Profilo, utente_id: str, messaggio: str) -> None:
    # Un admin che si blocca da solo resterebbe chiuso fuori dal pannello.
    if utente_id == admin.id:
        raise HTTPException(status_code=400, detail=messaggio)


def _errore(e: Exception, contesto: str):
    if isinstance(e, HTTPException):
        raise e
    if migrazione_mancante(e):
        raise HTTPException(status_code=503, detail=MIGRAZIONE_DA_ESEGUIRE)
    logger.error(f"Errore {contesto}: {traceback.format_exc()}")
    raise_db_error(e)


@router.get("/riepilogo")
def riepilogo(admin: Profilo = Depends(richiedi_admin)):
    """Numero di account in attesa di approvazione (per il contatore nel menu)."""
    try:
        res = supabase.table("users").select("id", count="exact").eq("stato", "in_attesa").limit(1).execute()
        return {"in_attesa": res.count or 0}
    except Exception as e:
        _errore(e, "riepilogo admin")


@router.get("/utenti/elenco")
def elenco_utenti(
    q: Optional[str] = Query(None),
    stato: Optional[Literal["in_attesa", "attivo", "bloccato"]] = Query(None),
    ruolo: Optional[Literal["admin", "user"]] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(PER_PAGINA, ge=1, le=PER_PAGINA_MAX),
    admin: Profilo = Depends(richiedi_admin),
):
    """Tutti gli utenti della piattaforma, prima quelli da approvare, poi i piu' recenti."""
    try:
        risultato = supabase.rpc("admin_utenti_elenco", {
            "p_q": " ".join(parole_ricerca(q)) or None,
            "p_stato": stato,
            "p_ruolo": ruolo,
            "p_limit": limit,
            "p_offset": offset,
        }).execute().data
        return {**risultato, "offset": offset, "limit": limit}
    except Exception as e:
        _errore(e, "elenco utenti admin")


@router.put("/utenti/{utente_id}/stato")
def cambia_stato(utente_id: str, payload: StatoUtenteUpdate, admin: Profilo = Depends(richiedi_admin)):
    """Abilita un account (in attesa o bloccato) oppure lo blocca: vale dalla richiesta successiva,
    anche se l'utente e' gia' dentro il gestionale."""
    _non_su_se_stessi(admin, utente_id, "Non puoi bloccare o sbloccare il tuo stesso account.")
    try:
        _utente_o_404(utente_id)
        supabase.table("users").update({"stato": payload.stato}).eq("id", utente_id).execute()
        dimentica_profilo(utente_id)
        logger.info(f"Admin {admin.id}: stato dell'utente {utente_id} -> {payload.stato}")
        return _utente_o_404(utente_id)
    except Exception as e:
        _errore(e, "cambio stato utente")


@router.post("/utenti/{utente_id}/visualizza")
def inizia_visualizzazione(utente_id: str, admin: Profilo = Depends(richiedi_admin)):
    """Controlla che l'account si possa visualizzare e ne restituisce i dati. Da qui in poi il
    frontend manda l'header X-Visualizza-Utente, verificato dal backend a ogni richiesta."""
    if utente_id == admin.id:
        raise HTTPException(status_code=400, detail="Questo è già il tuo account.")
    try:
        utente = _utente_o_404(utente_id)
        profilo = carica_profilo(utente_id, fresco=True)
        if not profilo or profilo.ruolo == "admin":
            raise HTTPException(status_code=400, detail="Gli account amministratore non si possono visualizzare.")
        logger.info(f"Admin {admin.id}: inizia la visualizzazione dell'account {utente_id}")
        return utente
    except Exception as e:
        _errore(e, "inizio visualizzazione")
