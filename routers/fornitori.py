import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from models.entities import FornitoreCreate, FornitoreResponse, FornitoreUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error

router = APIRouter(prefix="/fornitori", tags=["fornitori"])

@router.post("/", response_model=FornitoreResponse)
def create_fornitore(forn: FornitoreCreate, company_id: str = Depends(get_current_company_id)):
    try:
        data = forn.model_dump()
        data["azienda_id"] = company_id
        
        res = supabase.table("fornitori").insert(data).execute()
        if not res.data:
            raise HTTPException(status_code=400, detail="Failed to create fornitore")
            
        return res.data[0]
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/", response_model=List[FornitoreResponse])
def get_fornitori(company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("fornitori").select("*").eq("azienda_id", company_id).execute()
        return res.data
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{fornitore_id}", response_model=FornitoreResponse)
def update_fornitore(fornitore_id: str, forn: FornitoreUpdate, company_id: str = Depends(get_current_company_id)):
    try:
        data = forn.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

        res = supabase.table("fornitori").update(data).eq("id", fornitore_id).eq("azienda_id", company_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Fornitore non trovato")
        return res.data[0]
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.delete("/{fornitore_id}")
def delete_fornitore(fornitore_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("fornitori").delete().eq("id", fornitore_id).eq("azienda_id", company_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Fornitore non trovato")
        return {"message": "Fornitore eliminato"}
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)
