from dataclasses import dataclass
from typing import Optional
import threading
import time
import uuid

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase_auth.errors import AuthApiError
from utils.supabase_client import supabase, SUPABASE_URL
import logging
import traceback

logger = logging.getLogger(__name__)

security = HTTPBearer()

# I token di accesso di Supabase sono firmati con una chiave asimmetrica (ES256): si possono
# verificare qui con la chiave pubblica, senza chiedere a Supabase a ogni richiesta (~110 ms
# risparmiati per chiamata). Le chiavi pubbliche si scaricano una volta e restano in memoria; se
# arriva un token firmato con una chiave nuova (rotazione) PyJWKClient le riscarica da solo.
_EMITTENTE = f"{SUPABASE_URL}/auth/v1"
_ALGORITMI_ASIMMETRICI = {"ES256", "RS256"}
_chiavi_pubbliche = PyJWKClient(f"{_EMITTENTE}/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=10)

# Per quanto tempo si ricorda il profilo di un utente (azienda, ruolo, stato) invece di rileggerlo
# dal database a ogni richiesta (~110 ms). Quando un admin abilita, blocca o cambia ruolo a un
# utente il profilo si dimentica subito (dimentica_profilo); il limite copre gli altri casi.
_DURATA_CACHE_PROFILO = 30
_cache_profili: dict[str, tuple["Profilo", float]] = {}
_cache_lock = threading.Lock()

# Errori di PostgREST quando la migrazione 20261106_ruoli_e_approvazione.sql non e' ancora stata
# eseguita (colonna, relazione o funzione mancante): in quel caso tutto funziona come prima,
# cioe' ogni utente e' attivo e nessuno e' admin.
ERRORI_MIGRAZIONE_MANCANTE = {"42703", "PGRST200", "PGRST202", "PGRST204"}

# Header con cui un admin chiede di lavorare come un altro utente ("Visualizza" nel pannello Admin).
HEADER_VISUALIZZA = "X-Visualizza-Utente"

MESSAGGI_STATO = {
    "in_attesa": "Il tuo account è in attesa di approvazione: potrai accedere appena un amministratore lo avrà abilitato.",
    "bloccato": "Il tuo account è stato bloccato. Per informazioni contatta l'amministratore.",
}


@dataclass(frozen=True)
class UtenteAutenticato:
    id: str
    email: Optional[str] = None


@dataclass(frozen=True)
class Profilo:
    id: str
    company_id: str
    ruolo: str  # "admin" | "user"
    stato: str  # "in_attesa" | "attivo" | "bloccato"


@dataclass(frozen=True)
class Contesto:
    """Per conto di chi si lavora in questa richiesta.

    `utente` e' l'account di cui si leggono e modificano i dati. Di norma e' chi ha fatto
    l'accesso; quando un admin usa "Visualizza", e' l'utente visualizzato e `admin` e' l'admin."""
    utente: Profilo
    admin: Optional[Profilo] = None


def migrazione_mancante(e: Exception) -> bool:
    return getattr(e, "code", None) in ERRORI_MIGRAZIONE_MANCANTE


def _leggi_profilo(user_id: str) -> Optional[Profilo]:
    try:
        righe = supabase.table("users").select("company_id, stato, role(codice)").eq("id", user_id).execute().data
    except Exception as e:
        if not migrazione_mancante(e):
            raise
        righe = supabase.table("users").select("company_id").eq("id", user_id).execute().data
        return Profilo(id=user_id, company_id=righe[0]["company_id"], ruolo="user", stato="attivo") if righe else None
    if not righe:
        return None
    riga = righe[0]
    return Profilo(
        id=user_id,
        company_id=riga["company_id"],
        ruolo=(riga.get("role") or {}).get("codice") or "user",
        stato=riga.get("stato") or "attivo",
    )


def carica_profilo(user_id: str, fresco: bool = False) -> Optional[Profilo]:
    """Profilo dell'utente (None se non esiste). `fresco` salta la cache (es. al login)."""
    adesso = time.monotonic()
    if not fresco:
        with _cache_lock:
            in_memoria = _cache_profili.get(user_id)
        if in_memoria and in_memoria[1] > adesso:
            return in_memoria[0]
    profilo = _leggi_profilo(user_id)
    if profilo:
        with _cache_lock:
            _cache_profili[user_id] = (profilo, adesso + _DURATA_CACHE_PROFILO)
    return profilo


def dimentica_profilo(user_id: str) -> None:
    """Da chiamare dopo aver cambiato stato o ruolo di un utente: vale dalla richiesta successiva."""
    with _cache_lock:
        _cache_profili.pop(user_id, None)


def errore_stato_account(stato: str) -> HTTPException:
    """403 per un account non attivo. `codice` permette al frontend di disconnettere l'utente e
    spiegargli il motivo nella pagina di accesso."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"codice": f"account_{stato}", "messaggio": MESSAGGI_STATO.get(stato, "Account non attivo.")},
    )


def _non_autorizzato(dettaglio: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=dettaglio,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _verifica_locale(token: str) -> Optional[UtenteAutenticato]:
    """Verifica firma, scadenza, emittente e destinatario del token senza chiamate di rete.

    Ritorna None quando la verifica locale non e' possibile (token firmato con il vecchio segreto
    condiviso HS256, oppure chiavi pubbliche momentaneamente non scaricabili): in quel caso decide
    Supabase, come prima."""
    try:
        algoritmo = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        raise _non_autorizzato("Token non valido")
    if algoritmo not in _ALGORITMI_ASIMMETRICI:
        return None

    try:
        chiave = _chiavi_pubbliche.get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientError as e:
        logger.warning(f"Chiavi pubbliche Supabase non disponibili, verifica tramite Supabase: {e}")
        return None

    try:
        dati = jwt.decode(
            token,
            chiave,
            algorithms=[algoritmo],
            audience="authenticated",
            issuer=_EMITTENTE,
            leeway=10,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        # Condizione attesa (i token durano 1 ora): il frontend riceve 401, rinnova il token da solo
        # e ripete la richiesta (vedi lib/api.ts).
        raise _non_autorizzato("Token scaduto")
    except jwt.InvalidTokenError as e:
        logger.info(f"Token rifiutato: {e}")
        raise _non_autorizzato("Token non valido")
    return UtenteAutenticato(id=dati["sub"], email=dati.get("email"))


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UtenteAutenticato:
    token = credentials.credentials
    utente = _verifica_locale(token)
    if utente:
        return utente

    try:
        user_response = supabase.auth.get_user(token)
        if not user_response.user:
            raise _non_autorizzato("Invalid authentication credentials")
        return UtenteAutenticato(id=user_response.user.id, email=user_response.user.email)
    except HTTPException:
        raise
    except AuthApiError as e:
        # Token scaduto/revocato/malformato: condizione ATTESA e frequente (gli access token
        # Supabase durano solo 1 ora), non un bug del server - il frontend intercetta il 401,
        # rinnova il token da solo e ripete la richiesta in automatico (vedi lib/api.ts). Un log
        # minimo basta: un traceback completo per un evento di routine intasa i log e nasconde
        # gli errori veri.
        logger.info(f"Token non valido in get_current_user (gestito dal refresh automatico del frontend): {e}")
        raise _non_autorizzato(f"Authentication failed: {str(e)}")
    except Exception as e:
        # Qui invece e' un errore davvero inatteso (es. Supabase irraggiungibile): merita
        # traceback completo e va tenuto d'occhio.
        err_msg = f"Authentication failed: {str(e)}"
        logger.error(f"Error in get_current_user: {str(e)}")
        logger.error(traceback.format_exc())
        with open("auth_error.log", "a") as f:
            f.write(err_msg + "\n" + traceback.format_exc() + "\n")
        raise _non_autorizzato(err_msg)


def get_profilo(user: UtenteAutenticato = Depends(get_current_user)) -> Profilo:
    """Profilo di chi ha fatto l'accesso, solo se l'account e' attivo: un account in attesa o
    bloccato viene fermato a ogni richiesta, anche con un token ancora valido."""
    try:
        profilo = carica_profilo(user.id)
    except Exception as e:
        logger.error(f"Errore lettura profilo utente: {traceback.format_exc()}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error fetching user data: {e}")
    if not profilo:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company")
    if profilo.stato != "attivo":
        raise errore_stato_account(profilo.stato)
    return profilo


def _errore_visualizzazione(codice: str, messaggio: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"codice": codice, "messaggio": messaggio})


def get_contesto(request: Request, profilo: Profilo = Depends(get_profilo)) -> Contesto:
    """Account per conto del quale si lavora. Con l'header X-Visualizza-Utente un admin lavora
    come quell'utente (pulsante "Visualizza" del pannello Admin): lo si verifica a ogni richiesta,
    quindi chi non e' admin (o non lo e' piu') non puo' usarlo."""
    visualizzato_id = (request.headers.get(HEADER_VISUALIZZA) or "").strip()
    if not visualizzato_id or visualizzato_id == profilo.id:
        return Contesto(utente=profilo)

    if profilo.ruolo != "admin":
        raise _errore_visualizzazione(
            "visualizzazione_non_consentita", "Solo un amministratore può visualizzare l'account di un altro utente."
        )
    try:
        uuid.UUID(visualizzato_id)
        visualizzato = carica_profilo(visualizzato_id)
    except ValueError:
        visualizzato = None
    if not visualizzato or visualizzato.ruolo == "admin":
        raise _errore_visualizzazione("visualizzazione_non_valida", "Questo account non è disponibile per la visualizzazione.")
    return Contesto(utente=visualizzato, admin=profilo)


def get_current_company_id(contesto: Contesto = Depends(get_contesto)) -> str:
    """Azienda di cui si leggono e modificano i dati (quella dell'utente visualizzato, se un admin
    sta usando "Visualizza")."""
    return contesto.utente.company_id


def richiedi_admin(profilo: Profilo = Depends(get_profilo)) -> Profilo:
    """Per le funzioni del pannello Admin: sempre l'identita' reale, mai quella visualizzata."""
    if profilo.ruolo != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Funzione riservata agli amministratori.")
    return profilo
