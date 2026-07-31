import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from models.entities import ClientCreate, ClientResponse, ClientUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error

router = APIRouter(prefix="/clienti", tags=["clienti"])

@router.post("/", response_model=ClientResponse)
def create_client(client: ClientCreate, company_id: str = Depends(get_current_company_id)):
    try:
        data = client.model_dump()
        data["company_id"] = company_id
        
        res = supabase.table("clients").insert(data).execute()
        if not res.data:
            raise HTTPException(status_code=400, detail="Failed to create client")
            
        return res.data[0]
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/", response_model=List[ClientResponse])
def get_clients(company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("clients").select("*").eq("company_id", company_id).execute()
        return res.data
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{client_id}", response_model=ClientResponse)
def update_client(client_id: str, client: ClientUpdate, company_id: str = Depends(get_current_company_id)):
    try:
        data = client.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

        res = supabase.table("clients").update(data).eq("id", client_id).eq("company_id", company_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Cliente non trovato")
        return res.data[0]
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.delete("/{client_id}")
def delete_client(client_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("clients").delete().eq("id", client_id).eq("company_id", company_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Cliente non trovato")
        return {"message": "Cliente eliminato"}
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)
