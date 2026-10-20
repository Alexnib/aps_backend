import traceback
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from models.entities import SubappaltatoreCreate, SubappaltatoreResponse, SubappaltatoreUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error

logger = logging.getLogger(__name__)

# Anagrafica delle ditte subappaltatrici (vedi SAL Sub), separata da quella dei fornitori.
router = APIRouter(prefix="/subappaltatori", tags=["subappaltatori"])


@router.get("/", response_model=List[SubappaltatoreResponse])
def lista_subappaltatori(company_id: str = Depends(get_current_company_id)):
    try:
        res = (
            supabase.table("subappaltatori")
            .select("*")
            .eq("azienda_id", company_id)
            .order("ragione_sociale")
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Errore lettura subappaltatori: {traceback.format_exc()}")
        raise_db_error(e)


@router.post("/", response_model=SubappaltatoreResponse)
def crea_subappaltatore(payload: SubappaltatoreCreate, company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("subappaltatori").insert({**payload.model_dump(), "azienda_id": company_id}).execute()
        return res.data[0]
    except Exception as e:
        logger.error(f"Errore creazione subappaltatore: {traceback.format_exc()}")
        raise_db_error(e)


@router.put("/{subappaltatore_id}", response_model=SubappaltatoreResponse)
def aggiorna_subappaltatore(
    subappaltatore_id: str, payload: SubappaltatoreUpdate, company_id: str = Depends(get_current_company_id)
):
    dati = payload.model_dump(exclude_unset=True)
    if not dati:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")
    if "ragione_sociale" in dati and not dati["ragione_sociale"]:
        raise HTTPException(status_code=422, detail="La ragione sociale è obbligatoria")
    try:
        res = (
            supabase.table("subappaltatori")
            .update(dati)
            .eq("id", subappaltatore_id)
            .eq("azienda_id", company_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"Errore aggiornamento subappaltatore: {traceback.format_exc()}")
        raise_db_error(e)
    if not res.data:
        raise HTTPException(status_code=404, detail="Subappaltatore non trovato")
    return res.data[0]


@router.delete("/{subappaltatore_id}")
def elimina_subappaltatore(subappaltatore_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        res = (
            supabase.table("subappaltatori")
            .delete()
            .eq("id", subappaltatore_id)
            .eq("azienda_id", company_id)
            .execute()
        )
    except Exception as e:
        # Il vincolo sul database impedisce di eliminare una ditta che ha ancora lavorazioni affidate.
        if "23503" in str(e):
            raise HTTPException(
                status_code=409,
                detail="Questa ditta ha lavorazioni affidate in SAL Sub: elimina prima quegli affidamenti.",
            )
        logger.error(f"Errore eliminazione subappaltatore: {traceback.format_exc()}")
        raise_db_error(e)
    if not res.data:
        raise HTTPException(status_code=404, detail="Subappaltatore non trovato")
    return {"message": "Subappaltatore eliminato"}
