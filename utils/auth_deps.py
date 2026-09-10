from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase_auth.errors import AuthApiError
from utils.supabase_client import supabase
import logging
import traceback

logger = logging.getLogger(__name__)

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        # Verify token and get user from Supabase
        user_response = supabase.auth.get_user(token)
        if not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_response.user
    except HTTPException:
        raise
    except AuthApiError as e:
        # Token scaduto/revocato/malformato: condizione ATTESA e frequente (gli access token
        # Supabase durano solo 1 ora), non un bug del server - il frontend intercetta il 401,
        # rinnova il token da solo e ripete la richiesta in automatico (vedi lib/api.ts). Un log
        # minimo basta: un traceback completo per un evento di routine intasa i log e nasconde
        # gli errori veri.
        logger.info(f"Token non valido in get_current_user (gestito dal refresh automatico del frontend): {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        # Qui invece e' un errore davvero inatteso (es. Supabase irraggiungibile): merita
        # traceback completo e va tenuto d'occhio.
        err_msg = f"Authentication failed: {str(e)}"
        logger.error(f"Error in get_current_user: {str(e)}")
        logger.error(traceback.format_exc())
        with open("auth_error.log", "a") as f:
            f.write(err_msg + "\n" + traceback.format_exc() + "\n")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=err_msg,
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_company_id(user=Depends(get_current_user)) -> str:
    """
    Retrieves the company_id associated with the currently authenticated user.
    Assumes a public.users table maps auth.users.id to company_id.
    """
    try:
        response = supabase.table("users").select("company_id").eq("id", user.id).execute()
        
        if not response.data or len(response.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not belong to any company"
            )
        return response.data[0]["company_id"]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching company data: {str(e)}"
        )
