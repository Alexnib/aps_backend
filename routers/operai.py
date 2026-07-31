import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from models.entities import OperaioCreate, OperaioResponse, OperaioUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error

router = APIRouter(prefix="/operai", tags=["operai"])

@router.post("/", response_model=OperaioResponse)
def create_operaio(operaio: OperaioCreate, company_id: str = Depends(get_current_company_id)):
    try:
        # 1. Get or create tipologia risorsa for OPERAIO
        tip_res = supabase.table("tipologie_risorse").select("id").eq("codice", "OPERAIO").execute()
        if not tip_res.data:
            new_tip = supabase.table("tipologie_risorse").insert({"codice": "OPERAIO", "descrizione": "Operaio"}).execute()
            tipologia_id = new_tip.data[0]["id"]
        else:
            tipologia_id = tip_res.data[0]["id"]

        # 2. Create Risorsa
        codice_res = f"OP-{operaio.nome[:2].upper()}{operaio.cognome[:2].upper()}"
        risorsa_data = {
            "company_id": company_id,
            "tipologia_risorsa_id": tipologia_id,
            "codice_risorsa": codice_res,
            "nome_risorsa": f"{operaio.nome} {operaio.cognome}",
            "unita_misura": "h",
            "importo_unitario_standard": operaio.costo_orario
        }
        risorsa_res = supabase.table("risorse").insert(risorsa_data).execute()
        if not risorsa_res.data:
            raise HTTPException(status_code=500, detail="Failed to create risorsa for operaio")
        risorsa_id = risorsa_res.data[0]["id"]

        # 3. Create Operaio
        operaio_data = {
            "company_id": company_id,
            "risorsa_id": risorsa_id,
            "matricola": operaio.matricola,
            "nome": operaio.nome,
            "cognome": operaio.cognome
        }
        operaio_res = supabase.table("operai").insert(operaio_data).execute()
        if not operaio_res.data:
            raise HTTPException(status_code=500, detail="Failed to create operaio")
            
        result = operaio_res.data[0]
        result["costo_orario"] = operaio.costo_orario
        
        return result
        
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/", response_model=List[OperaioResponse])
def get_operai(company_id: str = Depends(get_current_company_id)):
    try:
        res = supabase.table("operai").select("*, risorse(importo_unitario_standard)").eq("company_id", company_id).execute()
        
        operai_list = []
        for row in res.data:
            operai_list.append({
                "id": row["id"],
                "risorsa_id": row["risorsa_id"],
                "matricola": row["matricola"],
                "nome": row["nome"],
                "cognome": row["cognome"],
                "costo_orario": row["risorse"]["importo_unitario_standard"] if row.get("risorse") else 0.0
            })

        return operai_list
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{operaio_id}", response_model=OperaioResponse)
def update_operaio(operaio_id: str, operaio: OperaioUpdate, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("operai").select("*").eq("id", operaio_id).eq("company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Operaio non trovato")
        current = existing.data[0]

        update_data = operaio.model_dump(exclude_unset=True)
        costo_orario = update_data.pop("costo_orario", None)

        if update_data:
            supabase.table("operai").update(update_data).eq("id", operaio_id).execute()

        if costo_orario is not None:
            supabase.table("risorse").update({"importo_unitario_standard": costo_orario}).eq("id", current["risorsa_id"]).execute()

        res = supabase.table("operai").select("*, risorse(importo_unitario_standard)").eq("id", operaio_id).execute()
        row = res.data[0]
        return {
            "id": row["id"],
            "risorsa_id": row["risorsa_id"],
            "matricola": row["matricola"],
            "nome": row["nome"],
            "cognome": row["cognome"],
            "costo_orario": row["risorse"]["importo_unitario_standard"] if row.get("risorse") else 0.0
        }
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)

@router.delete("/{operaio_id}")
def delete_operaio(operaio_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        existing = supabase.table("operai").select("risorsa_id").eq("id", operaio_id).eq("company_id", company_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Operaio non trovato")
        risorsa_id = existing.data[0]["risorsa_id"]

        supabase.table("operai").delete().eq("id", operaio_id).execute()
        if risorsa_id:
            supabase.table("risorse").delete().eq("id", risorsa_id).execute()

        return {"message": "Operaio eliminato"}
    except Exception as e:
        logger.error(f"Error in {__name__}: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException): raise e
        raise_db_error(e)
