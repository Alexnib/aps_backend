import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from datetime import date
from pydantic import BaseModel
from models.rapportini import (
    RapportinoOperaioCreate, RapportinoMezzoCreate, RapportinoResponse,
    RapportinoOperaioUpdate, RapportinoMezzoUpdate
)
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error

router = APIRouter(prefix="/rapportini", tags=["rapportini"])

@router.post("/operai", response_model=RapportinoResponse)
def create_rapportino_operaio(rap: RapportinoOperaioCreate, company_id: str = Depends(get_current_company_id)):
    try:
        op_res = supabase.table("operai").select("*, risorse(importo_unitario_standard)").eq("id", rap.operaio_id).execute()
        if not op_res.data:
            raise HTTPException(status_code=404, detail="Operaio non trovato")
        
        costo_orario = op_res.data[0]["risorse"]["importo_unitario_standard"]
        importo_totale = costo_orario * rap.quantita_ore

        data = {
            "cantiere_id": rap.cantiere_id,
            "operaio_id": rap.operaio_id,
            "data_lavoro": rap.data_lavoro.isoformat(),
            "quantita_ore": rap.quantita_ore,
            "importo_totale": importo_totale,
            "descrizione_lavorazione": rap.descrizione_lavorazione
        }
        res = supabase.table("rapportini_operai").insert(data).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Failed to create rapportino")

        created = res.data[0]
        return {
            "id": created["id"],
            "cantiere_id": created["cantiere_id"],
            "data": created["data_lavoro"],
            "quantita": created["quantita_ore"],
            "importo_totale": created["importo_totale"]
        }
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/operai", response_model=List[dict])
def get_rapportini_operai(company_id: str = Depends(get_current_company_id)):
    try:
        # We join with operai and cantieri
        res = supabase.table("rapportini_operai")\
            .select("*, operai!inner(company_id, nome, cognome), cantieri(nome_cantiere)")\
            .eq("operai.company_id", company_id).execute()
        return res.data
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/operai/{rapportino_id}", response_model=RapportinoResponse)
def update_rapportino_operaio(rapportino_id: str, rap: RapportinoOperaioUpdate, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("rapportini_operai")\
            .select("*, operai!inner(company_id)")\
            .eq("id", rapportino_id).eq("operai.company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Rapportino non trovato")
        current = existing.data[0]

        update_data = rap.model_dump(exclude_unset=True)
        if update_data.get("data_lavoro") is not None:
            update_data["data_lavoro"] = update_data["data_lavoro"].isoformat()
        if not update_data:
            raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

        # Recalculate importo_totale whenever the worker or the hours change
        if "operaio_id" in update_data or "quantita_ore" in update_data:
            operaio_id = update_data.get("operaio_id", current["operaio_id"])
            quantita_ore = update_data.get("quantita_ore", current["quantita_ore"])
            op_res = supabase.table("operai").select("*, risorse(importo_unitario_standard)").eq("id", operaio_id).execute()
            if not op_res.data:
                raise HTTPException(status_code=404, detail="Operaio non trovato")
            costo_orario = op_res.data[0]["risorse"]["importo_unitario_standard"]
            update_data["importo_totale"] = costo_orario * quantita_ore

        res = supabase.table("rapportini_operai").update(update_data).eq("id", rapportino_id).execute()
        created = res.data[0]
        return {
            "id": created["id"],
            "cantiere_id": created["cantiere_id"],
            "data": created["data_lavoro"],
            "quantita": created["quantita_ore"],
            "importo_totale": created["importo_totale"]
        }
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.delete("/operai/{rapportino_id}")
def delete_rapportino_operaio(rapportino_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("rapportini_operai")\
            .select("id, operai!inner(company_id)")\
            .eq("id", rapportino_id).eq("operai.company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Rapportino non trovato")

        supabase.table("rapportini_operai").delete().eq("id", rapportino_id).execute()
        return {"message": "Rapportino eliminato"}
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.post("/mezzi", response_model=RapportinoResponse)
def create_rapportino_mezzo(rap: RapportinoMezzoCreate, company_id: str = Depends(get_current_company_id)):
    try:
        mz_res = supabase.table("mezzi").select("*, risorse(importo_unitario_standard)").eq("id", rap.mezzo_id).execute()
        if not mz_res.data:
            raise HTTPException(status_code=404, detail="Mezzo non trovato")
        
        costo_orario = mz_res.data[0]["risorse"]["importo_unitario_standard"]
        importo_totale = costo_orario * rap.quantita_ore

        data = {
            "cantiere_id": rap.cantiere_id,
            "mezzo_id": rap.mezzo_id,
            "data_utilizzo": rap.data_utilizzo.isoformat(),
            "quantita_ore": rap.quantita_ore,
            "importo_totale": importo_totale
        }
        res = supabase.table("rapportini_mezzi").insert(data).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Failed to create rapportino")

        created = res.data[0]
        return {
            "id": created["id"],
            "cantiere_id": created["cantiere_id"],
            "data": created["data_utilizzo"],
            "quantita": created["quantita_ore"],
            "importo_totale": created["importo_totale"]
        }
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/mezzi", response_model=List[dict])
def get_rapportini_mezzi(company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("rapportini_mezzi")\
            .select("*, mezzi!inner(company_id, targa_matricola, descrizione), cantieri(nome_cantiere)")\
            .eq("mezzi.company_id", company_id).execute()
        return res.data
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/mezzi/{rapportino_id}", response_model=RapportinoResponse)
def update_rapportino_mezzo(rapportino_id: str, rap: RapportinoMezzoUpdate, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("rapportini_mezzi")\
            .select("*, mezzi!inner(company_id)")\
            .eq("id", rapportino_id).eq("mezzi.company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Rapportino non trovato")
        current = existing.data[0]

        update_data = rap.model_dump(exclude_unset=True)
        if update_data.get("data_utilizzo") is not None:
            update_data["data_utilizzo"] = update_data["data_utilizzo"].isoformat()
        if not update_data:
            raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

        # Recalculate importo_totale whenever the mezzo or the hours change
        if "mezzo_id" in update_data or "quantita_ore" in update_data:
            mezzo_id = update_data.get("mezzo_id", current["mezzo_id"])
            quantita_ore = update_data.get("quantita_ore", current["quantita_ore"])
            mz_res = supabase.table("mezzi").select("*, risorse(importo_unitario_standard)").eq("id", mezzo_id).execute()
            if not mz_res.data:
                raise HTTPException(status_code=404, detail="Mezzo non trovato")
            costo_orario = mz_res.data[0]["risorse"]["importo_unitario_standard"]
            update_data["importo_totale"] = costo_orario * quantita_ore

        res = supabase.table("rapportini_mezzi").update(update_data).eq("id", rapportino_id).execute()
        created = res.data[0]
        return {
            "id": created["id"],
            "cantiere_id": created["cantiere_id"],
            "data": created["data_utilizzo"],
            "quantita": created["quantita_ore"],
            "importo_totale": created["importo_totale"]
        }
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.delete("/mezzi/{rapportino_id}")
def delete_rapportino_mezzo(rapportino_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("rapportini_mezzi")\
            .select("id, mezzi!inner(company_id)")\
            .eq("id", rapportino_id).eq("mezzi.company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Rapportino non trovato")

        supabase.table("rapportini_mezzi").delete().eq("id", rapportino_id).execute()
        return {"message": "Rapportino eliminato"}
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

