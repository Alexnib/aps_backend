import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional
from models.entities import FornitoreCreate, FornitoreResponse, FornitoreUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.elenchi import PER_PAGINA, PER_PAGINA_MAX, filtra_ricerca, leggi_pagina

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

@router.get("/elenco")
def elenco_fornitori(
    q: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(PER_PAGINA, ge=1, le=PER_PAGINA_MAX),
    company_id: str = Depends(get_current_company_id),
):
    """Pagina della tabella Fornitori, in ordine alfabetico. Il GET "/" resta per i menu a tendina."""
    try:
        query = supabase.table("fornitori").select("*", count="exact").eq("azienda_id", company_id)
        query = filtra_ricerca(query, q, ["ragione_sociale", "partita_iva", "citta", "email"])
        return leggi_pagina(query.order("ragione_sociale"), offset, limit)
    except Exception as e:
        logger.error(f"Errore elenco fornitori: {traceback.format_exc()}")
        raise_db_error(e)

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
