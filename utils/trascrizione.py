"""Trascrizione dei messaggi vocali con Whisper (API di OpenAI): niente modelli da far girare sul
nostro server.

Configurazione: OPENAI_API_KEY; facoltativi OPENAI_TRANSCRIBE_MODEL (default whisper-1) e
OPENAI_TRANSCRIBE_URL (utile solo per i test).
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Formati che Whisper accetta, per tipo di file ricevuto da WhatsApp (i vocali sono ogg/opus).
_ESTENSIONI = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
}

# Whisper usa al massimo ~224 token di suggerimento: bastano i nomi principali.
_MAX_SUGGERIMENTO = 600


class TrascrizioneNonRiuscita(Exception):
    """Il vocale non si e' potuto trascrivere; il messaggio e' adatto a essere mostrato all'utente."""


def trascrivi(audio: bytes, mime: str, suggerimento: str = "") -> str:
    """Testo del vocale, in italiano. `suggerimento`: nomi di persone, cantieri e mezzi
    dell'azienda, che Whisper altrimenti scriverebbe a orecchio."""
    chiave = os.environ.get("OPENAI_API_KEY")
    if not chiave:
        raise TrascrizioneNonRiuscita("La trascrizione dei vocali non è ancora attiva: per ora scrivimi un messaggio di testo.")
    tipo = (mime or "").split(";")[0].strip().lower()
    estensione = _ESTENSIONI.get(tipo)
    if not estensione:
        raise TrascrizioneNonRiuscita("Non riesco ad ascoltare questo tipo di audio: mandami un vocale di WhatsApp o scrivimi.")

    try:
        risposta = httpx.post(
            os.environ.get("OPENAI_TRANSCRIBE_URL", "https://api.openai.com/v1/audio/transcriptions"),
            headers={"Authorization": f"Bearer {chiave}"},
            files={"file": (f"vocale.{estensione}", audio, tipo)},
            data={
                "model": os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1"),
                "language": "it",
                "response_format": "json",
                "prompt": suggerimento[:_MAX_SUGGERIMENTO],
            },
            timeout=90.0,
        )
        risposta.raise_for_status()
        testo = (risposta.json().get("text") or "").strip()
    except httpx.HTTPError as e:
        logger.error(f"Trascrizione del vocale non riuscita: {e}")
        raise TrascrizioneNonRiuscita("Non sono riuscito ad ascoltare il vocale: riprova tra poco o scrivimi.")
    if not testo:
        raise TrascrizioneNonRiuscita("Il vocale sembra vuoto: riprova parlando più vicino al telefono, o scrivimi.")
    return testo
