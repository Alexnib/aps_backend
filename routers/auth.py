from fastapi import APIRouter, HTTPException, status
from models.auth import UserRegisterRequest, UserLoginRequest, TokenResponse
from utils.supabase_client import supabase
import traceback
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

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
        
        # Get company id
        user_res = supabase.table("users").select("company_id").eq("id", user_id).execute()
        company_id = user_res.data[0]["company_id"] if user_res.data else ""

        return TokenResponse(
            access_token=auth_response.session.access_token,
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
