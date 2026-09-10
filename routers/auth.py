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
import traceback
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _company_id_per_utente(user_id: str) -> str:
    user_res = supabase.table("users").select("company_id").eq("id", user_id).execute()
    return user_res.data[0]["company_id"] if user_res.data else ""

@router.post("/register")
def register(request: UserRegisterRequest):
    try:
        # Use a fresh client for Auth so we don't contaminate the global admin client
        from utils.supabase_client import SUPABASE_URL, SUPABASE_KEY
        from supabase import create_client
        temp_auth_client = create_client(SUPABASE_URL, SUPABASE_KEY)

        # 1. SignUp with Supabase Auth
        auth_response = temp_auth_client.auth.sign_up({
            "email": request.email,
            "password": request.password
        })
        
        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Registration failed at Auth level")

        user_id = auth_response.user.id

        # 2. Insert into companies table using the global admin client
        company_data = {
            "partita_iva": request.partita_iva,
            "indirizzo": request.indirizzo
        }
        company_res = supabase.table("companies").insert(company_data).execute()
        
        if not company_res.data:
            # We should probably rollback user creation, but let's keep it simple for MVP
            raise HTTPException(status_code=500, detail="Failed to create company")
            
        company_id = company_res.data[0]["id"]

        # 3. Insert into users table
        user_data = {
            "id": user_id,
            "company_id": company_id,
            "nome": request.nome,
            "cognome": request.cognome,
            "ruolo": "admin"  # The company creator is admin
        }
        supabase.table("users").insert(user_data).execute()

        return {"message": "Registration successful", "user_id": user_id, "company_id": company_id}

    except Exception as e:
        logger.error(f"Error in register: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login", response_model=TokenResponse)
def login(request: UserLoginRequest):
    try:
        # Use a fresh client for Auth so we don't contaminate the global admin client
        from utils.supabase_client import SUPABASE_URL, SUPABASE_KEY
        from supabase import create_client
        temp_auth_client = create_client(SUPABASE_URL, SUPABASE_KEY)

        auth_response = temp_auth_client.auth.sign_in_with_password({
            "email": request.email,
            "password": request.password
        })
        
        if not auth_response.session:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user_id = auth_response.user.id
        company_id = _company_id_per_utente(user_id)

        return TokenResponse(
            access_token=auth_response.session.access_token,
            refresh_token=auth_response.session.refresh_token,
            token_type="bearer",
            user_id=user_id,
            company_id=company_id
        )
    except Exception as e:
        logger.error(f"Error in login: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=f"Login failed: {str(e)}")


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(request: RefreshTokenRequest):
    """Scambia un refresh_token valido per una coppia access_token/refresh_token nuova, senza
    richiedere di nuovo email/password. Il frontend la chiama automaticamente quando una richiesta
    fallisce con 401 per token scaduto (gli access token Supabase durano solo 1 ora)."""
    try:
        # Client "fresco" come per login/register: auth.refresh_session non deve toccare il
        # client admin globale usato per le query dati.
        from utils.supabase_client import SUPABASE_URL, SUPABASE_KEY
        from supabase import create_client
        temp_auth_client = create_client(SUPABASE_URL, SUPABASE_KEY)

        auth_response = temp_auth_client.auth.refresh_session(request.refresh_token)

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
