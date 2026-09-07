import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException

from models.materiali import ArticoloAnagraficaCreate, ArticoloAnagraficaUpdate, ConsegnaCreate, ConsegnaUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.materiali_helpers import (
    find_or_create_risorsa_materiale,
    find_or_create_articolo_fornitore,
    get_risorse_materiale_ids,
)

router = APIRouter(prefix="/materiali", tags=["materiali"])

ARTICOLO_SELECT = "*, fornitori(id, ragione_sociale), risorse(nome_risorsa)"


def _get_cantiere_o_404(cantiere_id: str, company_id: str) -> dict:
    res = (
        supabase.table("cantieri")
        .select("id")
        .eq("id", cantiere_id)
        .eq("company_id", company_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Cantiere non trovato")
    return res.data[0]


def _get_fornitore_o_404(fornitore_id: str, company_id: str) -> dict:
    res = (
        supabase.table("fornitori")
        .select("id")
        .eq("id", fornitore_id)
        .eq("azienda_id", company_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Fornitore non trovato")
    return res.data[0]


def _get_articolo_o_404(articolo_id: str, company_id: str) -> dict:
    res = (
        supabase.table("articoli_fornitori")
        .select("id, risorsa_id, fornitore_id")
        .eq("id", articolo_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Materiale non trovato")
    articolo = res.data[0]

    risorsa_res = (
        supabase.table("risorse")
        .select("id")
        .eq("id", articolo["risorsa_id"])
        .eq("company_id", company_id)
        .execute()
    )
    if not risorsa_res.data:
        raise HTTPException(status_code=404, detail="Materiale non trovato")
    return articolo


# ==================== ANAGRAFICA (catalogo materiali, unico per azienda) ====================


@router.get("/anagrafica")
def get_anagrafica(company_id: str = Depends(get_current_company_id)):
    try:
        risorsa_ids = get_risorse_materiale_ids(company_id)
        if not risorsa_ids:
            return []

        res = (
            supabase.table("articoli_fornitori")
            .select(ARTICOLO_SELECT)
            .in_("risorsa_id", risorsa_ids)
            .order("created_at", desc=True)
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Errore lettura anagrafica materiali: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/anagrafica")
def create_articolo_anagrafica(
    payload: ArticoloAnagraficaCreate, company_id: str = Depends(get_current_company_id)
):
    _get_fornitore_o_404(payload.fornitore_id, company_id)

    if not payload.nome_materiale.strip():
        raise HTTPException(status_code=400, detail="Il nome del materiale e' obbligatorio.")

    try:
        risorsa_id = find_or_create_risorsa_materiale(
            payload.nome_materiale, payload.unita_misura, company_id
        )
        articolo_id = find_or_create_articolo_fornitore(
            payload.fornitore_id,
            risorsa_id,
            payload.nome_materiale,
            payload.unita_misura,
            payload.codice,
        )

        update_data = {"importo_unitario": payload.importo_unitario, "unita_misura": payload.unita_misura}
        if payload.codice is not None:
            update_data["codice_articolo_fornitore"] = payload.codice
        supabase.table("articoli_fornitori").update(update_data).eq("id", articolo_id).execute()

        res = supabase.table("articoli_fornitori").select(ARTICOLO_SELECT).eq("id", articolo_id).execute()
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore creazione articolo anagrafica: {str(e)}")
        logger.error(traceback.format_exc())
        raise_db_error(e)


@router.put("/anagrafica/{articolo_id}")
def update_articolo_anagrafica(
    articolo_id: str, payload: ArticoloAnagraficaUpdate, company_id: str = Depends(get_current_company_id)
):
    articolo = _get_articolo_o_404(articolo_id, company_id)

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    try:
        nome_nuovo = data.get("descrizione_articolo")
        if nome_nuovo:
            supabase.table("risorse").update({"nome_risorsa": nome_nuovo}).eq(
                "id", articolo["risorsa_id"]
            ).execute()

        res = supabase.table("articoli_fornitori").update(data).eq("id", articolo_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Materiale non trovato")

        full = supabase.table("articoli_fornitori").select(ARTICOLO_SELECT).eq("id", articolo_id).execute()
        return full.data[0]
    except Exception as e:
        logger.error(f"Errore aggiornamento articolo anagrafica: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)


@router.delete("/anagrafica/{articolo_id}")
def delete_articolo_anagrafica(articolo_id: str, company_id: str = Depends(get_current_company_id)):
    _get_articolo_o_404(articolo_id, company_id)
    try:
        res = supabase.table("articoli_fornitori").delete().eq("id", articolo_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Materiale non trovato")
        return {"message": "Materiale eliminato dall'anagrafica"}
    except Exception as e:
        logger.error(f"Errore eliminazione articolo anagrafica: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)


# ==================== CONSEGNE PER CANTIERE (materiali realmente utilizzati) ====================


CONSEGNA_SELECT = (
    "*, articoli_fornitori(descrizione_articolo, unita_misura, codice_articolo_fornitore, fornitori(ragione_sociale))"
)


@router.get("/consegne")
def get_consegne_cantiere(cantiere_id: str, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(cantiere_id, company_id)
    try:
        res = (
            supabase.table("ddt_materiali")
            .select(CONSEGNA_SELECT)
            .eq("cantiere_id", cantiere_id)
            .order("data_consegna", desc=True)
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Errore lettura consegne cantiere: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/consegne")
def create_consegna(payload: ConsegnaCreate, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(payload.cantiere_id, company_id)
    articolo = _get_articolo_o_404(payload.articolo_fornitore_id, company_id)

    if payload.quantita <= 0:
        raise HTTPException(status_code=400, detail="La quantita' deve essere maggiore di zero.")

    try:
        prezzo_res = (
            supabase.table("articoli_fornitori").select("importo_unitario").eq("id", articolo["id"]).execute()
        )
        prezzo_unitario = prezzo_res.data[0]["importo_unitario"] if prezzo_res.data else 0

        row = {
            "cantiere_id": payload.cantiere_id,
            "articolo_fornitore_id": payload.articolo_fornitore_id,
            "numero_documento": payload.numero_documento or "Manuale",
            "data_consegna": payload.data_consegna,
            "quantita": payload.quantita,
            "importo_totale": round(payload.quantita * (prezzo_unitario or 0), 2),
        }
        res = supabase.table("ddt_materiali").insert(row).execute()
        if not res.data:
            raise HTTPException(status_code=400, detail="Impossibile registrare il materiale.")

        full = supabase.table("ddt_materiali").select(CONSEGNA_SELECT).eq("id", res.data[0]["id"]).execute()
        return full.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore creazione consegna manuale: {str(e)}")
        logger.error(traceback.format_exc())
        raise_db_error(e)


def _get_consegna_o_404(consegna_id: str, company_id: str) -> dict:
    res = (
        supabase.table("ddt_materiali")
        .select("id, cantiere_id, cantieri!inner(company_id)")
        .eq("id", consegna_id)
        .eq("cantieri.company_id", company_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Consegna non trovata")
    return res.data[0]


@router.put("/consegne/{consegna_id}")
def update_consegna(consegna_id: str, payload: ConsegnaUpdate, company_id: str = Depends(get_current_company_id)):
    _get_consegna_o_404(consegna_id, company_id)

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    if "quantita" in data and data["quantita"] is not None and data["quantita"] <= 0:
        raise HTTPException(status_code=400, detail="La quantita' deve essere maggiore di zero.")

    try:
        res = supabase.table("ddt_materiali").update(data).eq("id", consegna_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Consegna non trovata")

        full = supabase.table("ddt_materiali").select(CONSEGNA_SELECT).eq("id", consegna_id).execute()
        return full.data[0]
    except Exception as e:
        logger.error(f"Errore aggiornamento consegna: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)


@router.delete("/consegne/{consegna_id}")
def delete_consegna(consegna_id: str, company_id: str = Depends(get_current_company_id)):
    _get_consegna_o_404(consegna_id, company_id)
    try:
        res = supabase.table("ddt_materiali").delete().eq("id", consegna_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Consegna non trovata")
        return {"message": "Consegna eliminata"}
    except Exception as e:
        logger.error(f"Errore eliminazione consegna: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)
