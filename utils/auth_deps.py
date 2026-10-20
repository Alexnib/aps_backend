from dataclasses import dataclass
from typing import Optional
import threading
import time

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, status
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

# Per quanto tempo si ricorda l'azienda di un utente (altrimenti un'altra lettura dal database a
# ogni richiesta, ~110 ms). Un utente non cambia azienda; il limite serve solo perche' un account
# eliminato smetta di funzionare in tempi brevi.
_DURATA_CACHE_AZIENDA = 300
_cache_azienda: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class UtenteAutenticato:
    id: str
    email: Optional[str] = None


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


def get_current_company_id(user: UtenteAutenticato = Depends(get_current_user)) -> str:
    """
    Retrieves the company_id associated with the currently authenticated user.
    Assumes a public.users table maps auth.users.id to company_id.
    """
    adesso = time.monotonic()
    with _cache_lock:
        in_memoria = _cache_azienda.get(user.id)
    if in_memoria and in_memoria[1] > adesso:
        return in_memoria[0]

    try:
        response = supabase.table("users").select("company_id").eq("id", user.id).execute()

        if not response.data or len(response.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not belong to any company"
            )
        company_id = response.data[0]["company_id"]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching company data: {str(e)}"
        )

    with _cache_lock:
        _cache_azienda[user.id] = (company_id, adesso + _DURATA_CACHE_AZIENDA)
    return company_id
