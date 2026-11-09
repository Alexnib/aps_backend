"""Assistente WhatsApp: webhook di Meta e numeri collegati agli account.

Il webhook va registrato nel pannello Meta for Developers (WhatsApp > Configurazione) con
URL https://<backend>/whatsapp/webhook e come "verify token" il valore di WHATSAPP_VERIFY_TOKEN,
iscrivendo il campo "messages".
"""
import json
import logging
import os
import re
import traceback

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from models.whatsapp import NumeroWhatsappCreate
from utils.auth_deps import Contesto, get_contesto
from utils.errors import raise_db_error
from utils.supabase_client import supabase
from utils.whatsapp_api import configurato, firma_valida, phone_number_id
from utils.whatsapp_bot import gestisci_messaggio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

# Tabelle WhatsApp non ancora create (migrazione 20261107_whatsapp.sql da eseguire).
_TABELLA_MANCANTE = {"PGRST205", "42P01"}
MIGRAZIONE_DA_ESEGUIRE = (
    "L'assistente WhatsApp richiede la migrazione migrations/20261107_whatsapp.sql: eseguila nel SQL Editor di Supabase."
)


# ------------------------------------------------------------------ webhook di Meta

@router.get("/webhook")
def verifica_webhook(request: Request):
    """Verifica iniziale di Meta quando si registra il webhook: va restituito hub.challenge."""
    parametri = request.query_params
    atteso = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if parametri.get("hub.mode") == "subscribe" and atteso and parametri.get("hub.verify_token") == atteso:
        return PlainTextResponse(parametri.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Verifica del webhook non riuscita.")


@router.post("/webhook")
async def ricevi_webhook(request: Request, background: BackgroundTasks):
    """Messaggi in arrivo. Si risponde subito a Meta (altrimenti ripete l'invio) e si elabora in
    background; le notifiche di consegna e lettura dei nostri messaggi si ignorano."""
    corpo = await request.body()
    if not firma_valida(corpo, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Firma non valida.")
    try:
        dati = json.loads(corpo)
    except ValueError:
        raise HTTPException(status_code=400, detail="Corpo non valido.")

    nostro_numero = phone_number_id()
    for entry in dati.get("entry") or []:
        for change in entry.get("changes") or []:
            valore = change.get("value") or {}
            if (valore.get("metadata") or {}).get("phone_number_id") != nostro_numero:
                continue
            for messaggio in valore.get("messages") or []:
                background.add_task(gestisci_messaggio, messaggio)
    return {"ok": True}


# ------------------------------------------------------------------ numeri collegati all'account

def normalizza_numero(testo: str) -> str:
    """Numero nel formato di Meta: solo cifre, con prefisso internazionale, senza "+".
    "+39 333 123 4567", "0039 333-1234567" e "333 1234567" diventano tutti 393331234567."""
    cifre = re.sub(r"[\s\-./()]", "", testo or "")
    if cifre.startswith("+"):
        cifre = cifre[1:]
    elif cifre.startswith("00"):
        cifre = cifre[2:]
    elif len(cifre) == 10 and cifre.startswith("3"):
        cifre = "39" + cifre  # cellulare italiano scritto senza prefisso
    if not re.fullmatch(r"[0-9]{8,15}", cifre):
        raise HTTPException(
            status_code=422,
            detail=[{"type": "value_error", "loc": ["body", "numero"], "msg": "Numero non valido: scrivilo con il prefisso, es. +39 333 123 4567."}],
        )
    return cifre


def _errore_db(e: Exception):
    if getattr(e, "code", None) in _TABELLA_MANCANTE:
        raise HTTPException(status_code=503, detail=MIGRAZIONE_DA_ESEGUIRE)
    logger.error(f"Errore numeri WhatsApp: {traceback.format_exc()}")
    raise_db_error(e)


@router.get("/numeri")
def elenco_numeri(contesto: Contesto = Depends(get_contesto)):
    try:
        numeri = (
            supabase.table("whatsapp_numeri")
            .select("id, nome, numero, created_at")
            .eq("user_id", contesto.utente.id)
            .order("created_at")
            .execute()
            .data
        )
    except Exception as e:
        _errore_db(e)
    return {
        "numeri": numeri,
        # Numero dell'assistente da mostrare (e da salvare in rubrica), se configurato.
        "numero_assistente": os.environ.get("WHATSAPP_DISPLAY_NUMBER") or None,
        "attivo": configurato(),
    }


@router.post("/numeri")
def collega_numero(payload: NumeroWhatsappCreate, contesto: Contesto = Depends(get_contesto)):
    numero = normalizza_numero(payload.numero)
    try:
        res = supabase.table("whatsapp_numeri").insert({
            "user_id": contesto.utente.id,
            "nome": payload.nome.strip(),
            "numero": numero,
        }).execute()
        return res.data[0]
    except Exception as e:
        if getattr(e, "code", None) == "23505":
            raise HTTPException(
                status_code=409,
                detail=[{"type": "value_error", "loc": ["body", "numero"], "msg": "Questo numero è già collegato a un account APS Light."}],
            )
        _errore_db(e)


@router.delete("/numeri/{numero_id}")
def scollega_numero(numero_id: str, contesto: Contesto = Depends(get_contesto)):
    try:
        res = supabase.table("whatsapp_numeri").delete().eq("id", numero_id).eq("user_id", contesto.utente.id).execute()
    except Exception as e:
        _errore_db(e)
    if not res.data:
        raise HTTPException(status_code=404, detail="Numero non trovato.")
    return {"message": "Numero scollegato"}
