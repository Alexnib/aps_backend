"""Collegamento con la WhatsApp Cloud API di Meta: firma dei webhook, invio di messaggi, download
dei vocali.

Configurazione (variabili d'ambiente):
  WHATSAPP_TOKEN            chiave di accesso (permanente: utente di sistema in Meta Business)
  WHATSAPP_PHONE_NUMBER_ID  id del numero da cui risponde l'assistente (test o definitivo)
  WHATSAPP_APP_SECRET       "app secret" dell'app Meta: verifica che i webhook vengano da Meta
  WHATSAPP_VERIFY_TOKEN     parola scelta da noi, da inserire anche nel pannello Meta (webhook)
  WHATSAPP_DISPLAY_NUMBER   (facoltativo) numero da mostrare nel profilo, es. +1 555 123 4567
  WHATSAPP_API_VERSION      (facoltativo) versione della Graph API, default v23.0
  WHATSAPP_GRAPH_URL        (facoltativo) indirizzo della Graph API, utile solo per i test
Passare dal numero di test di Meta a quello definitivo = cambiare token e WHATSAPP_PHONE_NUMBER_ID.
"""
import hashlib
import hmac
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Limiti di WhatsApp: testo di un messaggio, testo di un messaggio con pulsanti, titolo pulsante.
MAX_TESTO = 4096
MAX_TESTO_PULSANTI = 1024
MAX_TITOLO_PULSANTE = 20

_http = httpx.Client(timeout=30.0)


def configurato() -> bool:
    return all(
        os.environ.get(nome)
        for nome in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN")
    )


def phone_number_id() -> Optional[str]:
    return os.environ.get("WHATSAPP_PHONE_NUMBER_ID")


def firma_valida(corpo: bytes, firma: Optional[str]) -> bool:
    """Il webhook arriva davvero da Meta: HMAC-SHA256 del corpo con l'app secret."""
    segreto = os.environ.get("WHATSAPP_APP_SECRET")
    if not segreto or not firma or not firma.startswith("sha256="):
        return False
    atteso = hmac.new(segreto.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(atteso, firma[len("sha256="):])


def _url(percorso: str) -> str:
    base = os.environ.get("WHATSAPP_GRAPH_URL", "https://graph.facebook.com").rstrip("/")
    versione = os.environ.get("WHATSAPP_API_VERSION", "v23.0")
    return f"{base}/{versione}/{percorso}"


def _intestazioni() -> dict:
    return {"Authorization": f"Bearer {os.environ.get('WHATSAPP_TOKEN', '')}"}


def _invia(numero: str, contenuto: dict) -> None:
    risposta = _http.post(
        _url(f"{phone_number_id()}/messages"),
        headers=_intestazioni(),
        json={"messaging_product": "whatsapp", "recipient_type": "individual", "to": numero, **contenuto},
    )
    if risposta.is_error:
        logger.error(f"WhatsApp: invio a {numero} fallito ({risposta.status_code}): {risposta.text[:500]}")
        risposta.raise_for_status()


def _dividi(testo: str, massimo: int) -> list[str]:
    """Divide un testo lungo in parti sotto il limite, possibilmente tra un paragrafo e l'altro."""
    parti: list[str] = []
    while len(testo) > massimo:
        taglio = testo.rfind("\n", 0, massimo)
        if taglio < massimo // 2:
            taglio = massimo
        parti.append(testo[:taglio].rstrip())
        testo = testo[taglio:].lstrip("\n")
    if testo.strip():
        parti.append(testo)
    return parti


def invia_testo(numero: str, testo: str) -> None:
    for parte in _dividi(testo.strip(), MAX_TESTO):
        _invia(numero, {"type": "text", "text": {"preview_url": False, "body": parte}})


def invia_pulsanti(numero: str, testo: str, pulsanti: list[tuple[str, str]]) -> None:
    """Messaggio con pulsanti di risposta rapida (massimo 3). `pulsanti`: (id, titolo).
    Un testo troppo lungo per il messaggio con pulsanti si manda prima, a parte."""
    testo = testo.strip()
    if len(testo) > MAX_TESTO_PULSANTI:
        invia_testo(numero, testo)
        testo = "Confermi?"
    _invia(numero, {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": testo},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": id_, "title": titolo[:MAX_TITOLO_PULSANTE]}}
                    for id_, titolo in pulsanti[:3]
                ]
            },
        },
    })


def segna_letto(wamid: str) -> None:
    """Spunte blu e "sta scrivendo..." mentre l'assistente prepara la risposta. Non essenziale:
    un errore qui non deve fermare la risposta."""
    try:
        _http.post(
            _url(f"{phone_number_id()}/messages"),
            headers=_intestazioni(),
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": wamid,
                "typing_indicator": {"type": "text"},
            },
        )
    except Exception as e:
        logger.info(f"WhatsApp: impossibile segnare come letto {wamid}: {e}")


def scarica_media(media_id: str) -> tuple[bytes, str]:
    """Contenuto e tipo di un file ricevuto (es. un vocale): prima l'indirizzo, poi il file."""
    info = _http.get(_url(media_id), headers=_intestazioni())
    info.raise_for_status()
    dati = info.json()
    file = _http.get(dati["url"], headers=_intestazioni())
    file.raise_for_status()
    return file.content, dati.get("mime_type") or file.headers.get("content-type", "")
