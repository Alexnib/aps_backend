import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from models.entities import CantiereCreate, CantiereResponse, CantiereUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.cantieri_helpers import fetch_percentuali_effettive

router = APIRouter(prefix="/cantieri", tags=["cantieri"])

@router.post("/", response_model=CantiereResponse)
def create_cantiere(cantiere: CantiereCreate, company_id: str = Depends(get_current_company_id)):
    try:
        data = cantiere.model_dump()
        data["company_id"] = company_id
        
        res = supabase.table("cantieri").insert(data).execute()
        if not res.data:
            raise HTTPException(status_code=400, detail="Failed to create cantiere")
            
        return res.data[0]
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/", response_model=List[CantiereResponse])
def get_cantieri(company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("cantieri").select("*").eq("company_id", company_id).execute()
        percentuali = fetch_percentuali_effettive(res.data)
        for c in res.data:
            pct, derivata = percentuali.get(c["id"], (0, False))
            c["percentuale_effettiva"] = pct if derivata else None
        return res.data
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{cantiere_id}", response_model=CantiereResponse)
def update_cantiere(cantiere_id: str, cantiere: CantiereUpdate, company_id: str = Depends(get_current_company_id)):
    try:
        data = cantiere.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

        res = supabase.table("cantieri").update(data).eq("id", cantiere_id).eq("company_id", company_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Cantiere non trovato")
        return res.data[0]
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.delete("/{cantiere_id}")
def delete_cantiere(cantiere_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("cantieri").delete().eq("id", cantiere_id).eq("company_id", company_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Cantiere non trovato")
        return {"message": "Cantiere eliminato"}
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)
