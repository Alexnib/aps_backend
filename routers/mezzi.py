import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from models.entities import MezzoCreate, MezzoResponse, MezzoUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error

router = APIRouter(prefix="/mezzi", tags=["mezzi"])

@router.post("/", response_model=MezzoResponse)
def create_mezzo(mezzo: MezzoCreate, company_id: str = Depends(get_current_company_id)):
    try:
        # 1. Get or create tipologia risorsa for MEZZO
        tip_res = supabase.table("tipologie_risorse").select("id").eq("codice", "MEZZO").execute()
        if not tip_res.data:
            new_tip = supabase.table("tipologie_risorse").insert({"codice": "MEZZO", "descrizione": "Mezzo d'opera"}).execute()
            tipologia_id = new_tip.data[0]["id"]
        else:
            tipologia_id = tip_res.data[0]["id"]

        # 2. Create Risorsa
        codice_res = f"MZ-{mezzo.targa_matricola.upper()}"
        risorsa_data = {
            "company_id": company_id,
            "tipologia_risorsa_id": tipologia_id,
            "codice_risorsa": codice_res,
            "nome_risorsa": mezzo.descrizione or mezzo.targa_matricola,
            "unita_misura": "h",
            "importo_unitario_standard": mezzo.costo_orario
        }
        risorsa_res = supabase.table("risorse").insert(risorsa_data).execute()
        if not risorsa_res.data:
            raise HTTPException(status_code=500, detail="Failed to create risorsa for mezzo")
        risorsa_id = risorsa_res.data[0]["id"]

        # 3. Create Mezzo
        mezzo_data = {
            "company_id": company_id,
            "risorsa_id": risorsa_id,
            "targa_matricola": mezzo.targa_matricola,
            "descrizione": mezzo.descrizione
        }
        mezzo_res = supabase.table("mezzi").insert(mezzo_data).execute()
        if not mezzo_res.data:
            raise HTTPException(status_code=500, detail="Failed to create mezzo")
            
        result = mezzo_res.data[0]
        result["costo_orario"] = mezzo.costo_orario
        
        return result
        
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/", response_model=List[MezzoResponse])
def get_mezzi(company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("mezzi").select("*, risorse(importo_unitario_standard)").eq("company_id", company_id).execute()
        
        mezzi_list = []
        for row in res.data:
            mezzi_list.append({
                "id": row["id"],
                "risorsa_id": row["risorsa_id"],
                "targa_matricola": row["targa_matricola"],
                "descrizione": row["descrizione"],
                "costo_orario": row["risorse"]["importo_unitario_standard"] if row.get("risorse") else 0.0
            })

        return mezzi_list
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{mezzo_id}", response_model=MezzoResponse)
def update_mezzo(mezzo_id: str, mezzo: MezzoUpdate, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("mezzi").select("*").eq("id", mezzo_id).eq("company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Mezzo non trovato")
        current = existing.data[0]

        update_data = mezzo.model_dump(exclude_unset=True)
        costo_orario = update_data.pop("costo_orario", None)

        if update_data:
            supabase.table("mezzi").update(update_data).eq("id", mezzo_id).execute()

        if costo_orario is not None:
            supabase.table("risorse").update({"importo_unitario_standard": costo_orario}).eq("id", current["risorsa_id"]).execute()

        res = supabase.table("mezzi").select("*, risorse(importo_unitario_standard)").eq("id", mezzo_id).execute()
        row = res.data[0]
        return {
            "id": row["id"],
            "risorsa_id": row["risorsa_id"],
            "targa_matricola": row["targa_matricola"],
            "descrizione": row["descrizione"],
            "costo_orario": row["risorse"]["importo_unitario_standard"] if row.get("risorse") else 0.0
        }
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.delete("/{mezzo_id}")
def delete_mezzo(mezzo_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("mezzi").select("risorsa_id").eq("id", mezzo_id).eq("company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Mezzo non trovato")
        risorsa_id = existing.data[0]["risorsa_id"]

        supabase.table("mezzi").delete().eq("id", mezzo_id).execute()
        if risorsa_id:
            supabase.table("risorse").delete().eq("id", risorsa_id).execute()

        return {"message": "Mezzo eliminato"}
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)
