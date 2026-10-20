import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional
from datetime import date
from pydantic import BaseModel
from models.rapportini import (
    RapportinoOperaioCreate, RapportinoMezzoCreate, RapportinoResponse,
    RapportinoOperaioUpdate, RapportinoMezzoUpdate
)
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.letture import leggi_tutte

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
def get_rapportini_operai(
    data: Optional[date] = Query(None, description="Solo le righe di questo giorno"),
    cantiere_id: Optional[str] = Query(None),
    company_id: str = Depends(get_current_company_id),
):
    try:
        def query():
            q = supabase.table("rapportini_operai")\
                .select("*, operai!inner(company_id, nome, cognome), cantieri(nome_cantiere)")\
                .eq("operai.company_id", company_id)
            if data: q = q.eq("data_lavoro", data.isoformat())
            if cantiere_id: q = q.eq("cantiere_id", cantiere_id)
            return q
        return leggi_tutte(query)
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
def get_rapportini_mezzi(
    data: Optional[date] = Query(None, description="Solo le righe di questo giorno"),
    cantiere_id: Optional[str] = Query(None),
    company_id: str = Depends(get_current_company_id),
):
    try:
        def query():
            q = supabase.table("rapportini_mezzi")\
                .select("*, mezzi!inner(company_id, targa_matricola, descrizione), cantieri(nome_cantiere)")\
                .eq("mezzi.company_id", company_id)
            if data: q = q.eq("data_utilizzo", data.isoformat())
            if cantiere_id: q = q.eq("cantiere_id", cantiere_id)
            return q
        return leggi_tutte(query)
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


# ==================== ELENCO PAGINATO ====================
# Un rapportino e' una giornata su un cantiere: righe di rapportini_operai e rapportini_mezzi con
# la stessa data e lo stesso cantiere. La paginazione la fa la funzione SQL rapportini_elenco
# (migrations/20261020_rapportini_elenco.sql); qui si leggono solo le righe dei rapportini della
# pagina richiesta.

def _righe_pagina(tabella: str, colonna_data: str, colonna_risorsa: str, select_risorsa: str,
                  company_id: str, cantieri: list, giorni: list, risorsa_id: Optional[str]) -> list[dict]:
    def query():
        q = (
            supabase.table(tabella)
            .select(f"id, {colonna_data}, cantiere_id, {colonna_risorsa}, quantita_ore, importo_totale, "
                    f"{select_risorsa}, cantieri(nome_cantiere)")
            .eq(f"{select_risorsa.split('!')[0]}.company_id", company_id)
            .in_("cantiere_id", cantieri)
            .gte(colonna_data, min(giorni))
            .lte(colonna_data, max(giorni))
        )
        return q.eq(colonna_risorsa, risorsa_id) if risorsa_id else q
    return leggi_tutte(query)


@router.get("/elenco")
def elenco_rapportini(
    da: Optional[date] = Query(None),
    a: Optional[date] = Query(None),
    cantiere_id: Optional[str] = Query(None),
    operaio_id: Optional[str] = Query(None),
    mezzo_id: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    company_id: str = Depends(get_current_company_id),
):
    try:
        riepilogo = supabase.rpc("rapportini_elenco", {
            "p_company_id": company_id,
            "p_da": da.isoformat() if da else None,
            "p_a": a.isoformat() if a else None,
            "p_cantiere_id": cantiere_id,
            "p_operaio_id": operaio_id,
            "p_mezzo_id": mezzo_id,
            "p_limit": limit,
            "p_offset": offset,
        }).execute().data

        chiavi = [(p["giorno"], p["cantiere_id"]) for p in riepilogo["pagina"]]
        rapportini = {
            chiave: {"data": chiave[0], "cantiere_id": chiave[1], "cantiere_nome": "-", "operai": [], "mezzi": []}
            for chiave in chiavi
        }

        if chiavi:
            giorni = [g for g, _ in chiavi]
            cantieri_pagina = list({c for _, c in chiavi})
            # Con un filtro su operaio e/o mezzo si mostrano solo le righe che corrispondono,
            # coerentemente con i totali calcolati dalla funzione SQL.
            filtro_righe = bool(operaio_id or mezzo_id)

            if not filtro_righe or operaio_id:
                for r in _righe_pagina("rapportini_operai", "data_lavoro", "operaio_id",
                                       "operai!inner(company_id, nome, cognome)",
                                       company_id, cantieri_pagina, giorni, operaio_id):
                    rapportino = rapportini.get((r["data_lavoro"], r["cantiere_id"]))
                    if rapportino is None:
                        continue
                    rapportino["cantiere_nome"] = (r.get("cantieri") or {}).get("nome_cantiere") or "-"
                    rapportino["operai"].append({
                        "id": r["id"],
                        "operaio_id": r["operaio_id"],
                        "nome": f"{r['operai']['nome']} {r['operai']['cognome']}",
                        "ore": float(r["quantita_ore"] or 0),
                        "importo": float(r["importo_totale"] or 0),
                    })

            if not filtro_righe or mezzo_id:
                for r in _righe_pagina("rapportini_mezzi", "data_utilizzo", "mezzo_id",
                                       "mezzi!inner(company_id, targa_matricola)",
                                       company_id, cantieri_pagina, giorni, mezzo_id):
                    rapportino = rapportini.get((r["data_utilizzo"], r["cantiere_id"]))
                    if rapportino is None:
                        continue
                    rapportino["cantiere_nome"] = (r.get("cantieri") or {}).get("nome_cantiere") or "-"
                    rapportino["mezzi"].append({
                        "id": r["id"],
                        "mezzo_id": r["mezzo_id"],
                        "nome": r["mezzi"]["targa_matricola"],
                        "ore": float(r["quantita_ore"] or 0),
                        "importo": float(r["importo_totale"] or 0),
                    })
    except Exception as e:
        logger.error(f"Errore elenco rapportini: {traceback.format_exc()}")
        raise_db_error(e)

    pagina = []
    for chiave in chiavi:
        rapportino = rapportini[chiave]
        rapportino["operai"].sort(key=lambda o: o["nome"].lower())
        rapportino["mezzi"].sort(key=lambda m: m["nome"].lower())
        righe = rapportino["operai"] + rapportino["mezzi"]
        rapportino["ore_totali"] = sum(x["ore"] for x in righe)
        rapportino["importo_totale"] = round(sum(x["importo"] for x in righe), 2)
        pagina.append(rapportino)

    return {
        "rapportini": pagina,
        "totale": riepilogo["totale"],
        "ore_totali": float(riepilogo["ore_totali"] or 0),
        "importo_totale": round(float(riepilogo["importo_totale"] or 0), 2),
        "offset": offset,
        "limit": limit,
    }
