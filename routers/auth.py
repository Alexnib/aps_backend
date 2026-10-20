from fastapi import APIRouter, Depends, HTTPException, status
from models.auth import (
    UserRegisterRequest,
    UserLoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    UserMeResponse,
    UserUpdateRequest,
)
from utils.supabase_client import supabase
from utils.auth_deps import get_current_user
from supabase_auth.errors import AuthApiError
import traceback
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _company_id_per_utente(user_id: str) -> str:
    user_res = supabase.table("users").select("company_id").eq("id", user_id).execute()
    return user_res.data[0]["company_id"] if user_res.data else ""

# Codici d'errore di Supabase Auth tradotti in risposte comprensibili. Per il login il messaggio
# sulle credenziali resta volutamente generico: non deve far capire se l'email esiste o meno.
_ERRORI_AUTH = {
    "invalid_credentials": (401, "Email o password non corretti."),
    "email_not_confirmed": (403, "Devi ancora confermare l'indirizzo email: controlla la posta (anche lo spam) e apri il link di conferma."),
    "user_already_exists": (409, "Esiste già un account con questa email. Accedi oppure usa un'altra email."),
    "email_exists": (409, "Esiste già un account con questa email. Accedi oppure usa un'altra email."),
    "weak_password": (422, "Password troppo debole o troppo comune: scegline una più lunga e meno prevedibile."),
    "email_address_invalid": (422, "Questo indirizzo email non è accettato. Usa un'email aziendale valida."),
    "signup_disabled": (403, "Le nuove registrazioni sono momentaneamente disattivate."),
    "over_request_rate_limit": (429, "Troppi tentativi ravvicinati. Attendi qualche minuto e riprova."),
    "over_email_send_rate_limit": (429, "Troppi tentativi ravvicinati. Attendi qualche minuto e riprova."),
    "user_banned": (403, "Questo account è stato disattivato. Contatta l'amministratore."),
}


def _errore_da_supabase(e: AuthApiError) -> HTTPException:
    codice = e.code
    # Alcune versioni di GoTrue non valorizzano `code` per le credenziali errate.
    if not codice and "invalid login credentials" in (e.message or "").lower():
        codice = "invalid_credentials"
    if codice in _ERRORI_AUTH:
        status_code, messaggio = _ERRORI_AUTH[codice]
        return HTTPException(status_code=status_code, detail=messaggio)
    if e.status == 429:
        return HTTPException(status_code=429, detail=_ERRORI_AUTH["over_request_rate_limit"][1])
    logger.error(f"Errore Supabase Auth non gestito: code={e.code} status={e.status} message={e.message}")
    return HTTPException(status_code=502, detail="Il servizio di autenticazione non risponde correttamente. Riprova tra qualche minuto.")


PIVA_GIA_REGISTRATA = (
    "Esiste già un account aziendale con questa partita IVA. Se ne fai parte, rivolgiti a chi l'ha registrato."
)


def _errore_campo(status_code: int, campo: str, messaggio: str) -> HTTPException:
    """Errore legato a un singolo campo, nello stesso formato dei 422 di FastAPI: il frontend lo
    mostra sotto il campo giusto invece che in un messaggio generico."""
    return HTTPException(
        status_code=status_code,
        detail=[{"type": "value_error", "loc": ["body", campo], "msg": messaggio}],
    )


def _nuovo_client_auth():
    # Client "fresco" per le operazioni di Auth: sign_in/sign_up salvano la sessione dentro il
    # client, e farlo su quello globale (service role) cambierebbe l'utente di tutte le query.
    from utils.supabase_client import SUPABASE_URL, SUPABASE_KEY
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@router.post("/register")
def register(request: UserRegisterRequest):
    # La partita IVA e' unica a livello di database: meglio scoprirlo prima di creare l'utente Auth.
    piva_esistente = (
        supabase.table("companies").select("id").eq("partita_iva", request.partita_iva).limit(1).execute()
    )
    if piva_esistente.data:
        raise _errore_campo(409, "partita_iva", PIVA_GIA_REGISTRATA)

    try:
        auth_response = _nuovo_client_auth().auth.sign_up({
            "email": request.email,
            "password": request.password
        })
    except AuthApiError as e:
        raise _errore_da_supabase(e)
    except Exception:
        logger.error(f"Error in register (sign_up): {traceback.format_exc()}")
        raise HTTPException(status_code=502, detail="Il servizio di autenticazione non è raggiungibile. Riprova tra qualche minuto.")

    utente = auth_response.user
    # Con la conferma email attiva Supabase non segnala un'email gia' registrata: restituisce un
    # utente "di facciata" senza identita' collegate.
    if not utente or not utente.identities:
        status_code, messaggio = _ERRORI_AUTH["user_already_exists"]
        raise HTTPException(status_code=status_code, detail=messaggio)

    user_id = utente.id
    company_id = None
    try:
        company_res = supabase.table("companies").insert({
            "partita_iva": request.partita_iva,
            "indirizzo": request.indirizzo,
        }).execute()
        company_id = company_res.data[0]["id"]

        supabase.table("users").insert({
            "id": user_id,
            "company_id": company_id,
            "nome": request.nome,
            "cognome": request.cognome,
            "ruolo": "admin",  # chi registra l'azienda ne e' l'amministratore
        }).execute()
    except Exception as e:
        logger.error(f"Error in register (profilo azienda): {traceback.format_exc()}")
        # Annulla quanto gia' creato: altrimenti resterebbe un utente Auth senza azienda, che non
        # potrebbe ne' usare l'app ne' registrarsi di nuovo con la stessa email.
        if company_id:
            try:
                supabase.table("companies").delete().eq("id", company_id).execute()
            except Exception:
                logger.error(f"Rollback azienda {company_id} non riuscito: {traceback.format_exc()}")
        try:
            supabase.auth.admin.delete_user(user_id)
        except Exception:
            logger.error(f"Rollback utente Auth {user_id} non riuscito: {traceback.format_exc()}")
        # Due registrazioni quasi simultanee con la stessa partita IVA superano entrambe il
        # controllo iniziale: la seconda viene fermata qui dal vincolo di unicita'.
        if getattr(e, "code", None) == "23505" and "partita_iva" in str(e):
            raise _errore_campo(409, "partita_iva", PIVA_GIA_REGISTRATA)
        raise HTTPException(status_code=500, detail="Non è stato possibile completare la registrazione. Riprova tra qualche minuto.")

    return {
        "message": "Registration successful",
        "user_id": user_id,
        "company_id": company_id,
        # Se Supabase richiede la conferma dell'email non c'e' ancora una sessione: il frontend lo
        # usa per dire all'utente di controllare la posta invece di tentare l'accesso.
        "conferma_email_richiesta": auth_response.session is None,
    }


@router.post("/login", response_model=TokenResponse)
def login(request: UserLoginRequest):
    try:
        auth_response = _nuovo_client_auth().auth.sign_in_with_password({
            "email": request.email,
            "password": request.password
        })
    except AuthApiError as e:
        raise _errore_da_supabase(e)
    except Exception:
        logger.error(f"Error in login: {traceback.format_exc()}")
        raise HTTPException(status_code=502, detail="Il servizio di autenticazione non è raggiungibile. Riprova tra qualche minuto.")

    if not auth_response.session:
        status_code, messaggio = _ERRORI_AUTH["invalid_credentials"]
        raise HTTPException(status_code=status_code, detail=messaggio)

    user_id = auth_response.user.id
    company_id = _company_id_per_utente(user_id)
    if not company_id:
        raise HTTPException(status_code=403, detail="Il tuo account non risulta collegato a nessuna azienda. Contatta l'amministratore.")

    return TokenResponse(
        access_token=auth_response.session.access_token,
        refresh_token=auth_response.session.refresh_token,
        token_type="bearer",
        user_id=user_id,
        company_id=company_id
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(request: RefreshTokenRequest):
    """Scambia un refresh_token valido per una coppia access_token/refresh_token nuova, senza
    richiedere di nuovo email/password. Il frontend la chiama automaticamente quando una richiesta
    fallisce con 401 per token scaduto (gli access token Supabase durano solo 1 ora)."""
    try:
        # Client "fresco" come per login/register: auth.refresh_session non deve toccare il
        # client admin globale usato per le query dati.
        auth_response = _nuovo_client_auth().auth.refresh_session(request.refresh_token)

        if not auth_response.session:
            raise HTTPException(status_code=401, detail="Sessione scaduta, effettua di nuovo il login.")

        user_id = auth_response.user.id
        company_id = _company_id_per_utente(user_id)

        return TokenResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            token_type="bearer",
            user_id=user_id,
            company_id=company_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Un refresh_token non valido/gia' usato/scaduto e' un caso normale (non un bug): risponde
        # sempre 401 cosi' il frontend sa che deve reindirizzare al login, senza loggare come errore.
        logger.info(f"Refresh token rifiutato: {str(e)}")
        raise HTTPException(status_code=401, detail="Sessione scaduta, effettua di nuovo il login.")


@router.get("/me", response_model=UserMeResponse)
def get_me(user=Depends(get_current_user)):
    try:
        user_res = (
            supabase.table("users")
            .select("id, company_id, nome, cognome, ruolo, companies(partita_iva, indirizzo)")
            .eq("id", user.id)
            .execute()
        )
        if not user_res.data:
            raise HTTPException(status_code=404, detail="Profilo utente non trovato")
        profilo = user_res.data[0]

        return UserMeResponse(
            id=profilo["id"],
            email=user.email,
            nome=profilo["nome"],
            cognome=profilo["cognome"],
            ruolo=profilo["ruolo"],
            company_id=profilo["company_id"],
            azienda=profilo.get("companies"),
        )
    except Exception as e:
        logger.error(f"Error in get_me: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/me", response_model=UserMeResponse)
def update_me(payload: UserUpdateRequest, user=Depends(get_current_user)):
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    try:
        res = supabase.table("users").update(data).eq("id", user.id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Profilo utente non trovato")

        return get_me(user=user)
    except Exception as e:
        logger.error(f"Error in update_me: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=str(e))
