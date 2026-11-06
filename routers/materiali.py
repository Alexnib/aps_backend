import traceback
import logging

logger = logging.getLogger(__name__)

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from postgrest.exceptions import APIError

from models.materiali import ArticoloAnagraficaCreate, ArticoloAnagraficaUpdate, ConsegnaCreate, ConsegnaUpdate
from utils.supabase_client import supabase
from utils.letture import leggi_tutte
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.elenchi import PER_PAGINA, PER_PAGINA_MAX, filtra_ricerca, leggi_pagina, parole_ricerca
from utils.materiali_helpers import (
    find_or_create_risorsa_materiale,
    find_or_create_articolo_fornitore,
    get_risorse_materiale_ids,
    get_tipologia_materiale_id,
)

router = APIRouter(prefix="/materiali", tags=["materiali"])

ARTICOLO_SELECT = "*, fornitori(id, ragione_sociale), risorse(nome_risorsa), cantieri(id, nome_cantiere)"


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
        .select("id, risorsa_id, fornitore_id, cantiere_id")
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

        return leggi_tutte(
            lambda: supabase.table("articoli_fornitori")
            .select(ARTICOLO_SELECT)
            .in_("risorsa_id", risorsa_ids)
            .order("created_at", desc=True)
        )
    except Exception as e:
        logger.error(f"Errore lettura anagrafica materiali: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/anagrafica/elenco")
def elenco_anagrafica(
    q: Optional[str] = Query(None),
    fornitore_id: Optional[str] = Query(None),
    validita: Optional[str] = Query(None, description='"tutte" = prezzo valido per tutte le commesse, altrimenti id del cantiere'),
    prezzo: Optional[Literal["senza", "con"]] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(PER_PAGINA, ge=1, le=PER_PAGINA_MAX),
    company_id: str = Depends(get_current_company_id),
):
    """Pagina dell'Anagrafica Articoli, dai piu' recenti. Il GET /anagrafica resta per i menu."""
    try:
        # L'azienda si riconosce dalla risorsa collegata (join interna, filtrata nel database).
        query = (
            supabase.table("articoli_fornitori")
            .select(
                "*, fornitori(id, ragione_sociale), risorse!inner(nome_risorsa), cantieri(id, nome_cantiere)",
                count="exact",
            )
            .eq("risorse.company_id", company_id)
            .eq("risorse.tipologia_risorsa_id", get_tipologia_materiale_id())
        )
        if fornitore_id:
            query = query.eq("fornitore_id", fornitore_id)
        if validita == "tutte":
            query = query.is_("cantiere_id", "null")
        elif validita:
            query = query.eq("cantiere_id", validita)
        if prezzo == "senza":
            query = query.or_("importo_unitario.is.null,importo_unitario.eq.0")
        elif prezzo == "con":
            query = query.gt("importo_unitario", 0)
        query = filtra_ricerca(query, q, ["descrizione_articolo", "codice_articolo_fornitore"])
        return leggi_pagina(query.order("created_at", desc=True), offset, limit)
    except Exception as e:
        logger.error(f"Errore elenco anagrafica materiali: {traceback.format_exc()}")
        raise_db_error(e)


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

    if data.get("fornitore_id"):
        _get_fornitore_o_404(data["fornitore_id"], company_id)
    if data.get("cantiere_id"):
        _get_cantiere_o_404(data["cantiere_id"], company_id)

    # Se cambia fornitore e/o commessa di validita', verifica che non esista gia' un'altra riga
    # identica (stesso fornitore + stesso materiale + stessa commessa): altrimenti si creerebbe un
    # doppione silenzioso invece di aggiornare quello giusto.
    if "fornitore_id" in data or "cantiere_id" in data:
        fornitore_finale = data.get("fornitore_id", articolo["fornitore_id"])
        cantiere_finale = data["cantiere_id"] if "cantiere_id" in data else articolo.get("cantiere_id")

        query = (
            supabase.table("articoli_fornitori")
            .select("id")
            .eq("fornitore_id", fornitore_finale)
            .eq("risorsa_id", articolo["risorsa_id"])
            .neq("id", articolo_id)
        )
        query = query.is_("cantiere_id", None) if not cantiere_finale else query.eq("cantiere_id", cantiere_finale)
        if query.execute().data:
            raise HTTPException(
                status_code=400,
                detail="Esiste gia' un prezzo per questo materiale con questo fornitore"
                + (" per questa commessa" if cantiere_finale else " valido per tutte le commesse")
                + ". Modifica o elimina quello esistente invece di crearne uno duplicato.",
            )

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
        return leggi_tutte(
            lambda: supabase.table("ddt_materiali")
            .select(CONSEGNA_SELECT)
            .eq("cantiere_id", cantiere_id)
            .order("data_consegna", desc=True)
        )
    except Exception as e:
        logger.error(f"Errore lettura consegne cantiere: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/consegne/elenco")
def elenco_consegne(
    cantiere_id: str,
    q: Optional[str] = Query(None),
    fornitore_id: Optional[str] = Query(None),
    da: Optional[date] = Query(None),
    a: Optional[date] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(PER_PAGINA, ge=1, le=PER_PAGINA_MAX),
    company_id: str = Depends(get_current_company_id),
):
    """Pagina dei materiali consegnati in un cantiere (pagina DDT), dai piu' recenti.

    Filtri, conteggio e importo totale li calcola la funzione SQL ddt_consegne_elenco
    (migrations/20261105_ddt_consegne_elenco.sql): la ricerca guarda sia il materiale sia il
    numero del documento, e il totale in euro riguarda tutte le consegne filtrate, non solo la
    pagina. Qui si leggono poi le sole righe della pagina.
    """
    _get_cantiere_o_404(cantiere_id, company_id)
    try:
        try:
            riepilogo = _riepilogo_consegne_sql(company_id, cantiere_id, q, fornitore_id, da, a, offset, limit)
        except APIError as e:
            if e.code != "PGRST202":  # PGRST202 = funzione SQL non (ancora) creata nel database
                raise
            logger.warning(
                "Funzione ddt_consegne_elenco assente: eseguire migrations/20261105_ddt_consegne_elenco.sql. "
                "Nel frattempo l'elenco DDT legge tutte le consegne del cantiere."
            )
            return _elenco_consegne_senza_funzione(cantiere_id, q, fornitore_id, da, a, offset, limit)

        ids = riepilogo["ids"]
        righe = (
            supabase.table("ddt_materiali").select(CONSEGNA_SELECT).in_("id", ids).execute().data
            if ids else []
        )
        per_id = {r["id"]: r for r in righe}
        return {
            "elementi": [per_id[i] for i in ids if i in per_id],
            "totale": riepilogo["totale"],
            "importo_totale": round(float(riepilogo["importo_totale"] or 0), 2),
            "offset": offset,
            "limit": limit,
        }
    except Exception as e:
        logger.error(f"Errore elenco consegne: {traceback.format_exc()}")
        raise_db_error(e)


def _riepilogo_consegne_sql(company_id, cantiere_id, q, fornitore_id, da, a, offset, limit) -> dict:
    """Totale, importo e id della pagina, calcolati dalla funzione SQL (vedi la migrazione)."""
    return supabase.rpc("ddt_consegne_elenco", {
        "p_company_id": company_id,
        "p_cantiere_id": cantiere_id,
        "p_q": " ".join(parole_ricerca(q)) or None,
        "p_fornitore_id": fornitore_id,
        "p_da": da.isoformat() if da else None,
        "p_a": a.isoformat() if a else None,
        "p_limit": limit,
        "p_offset": offset,
    }).execute().data


def _elenco_consegne_senza_funzione(cantiere_id, q, fornitore_id, da, a, offset, limit) -> dict:
    """Stesso risultato della funzione SQL, ma leggendo tutte le consegne del cantiere e filtrando
    qui: usato solo finche' la migrazione non e' stata eseguita (lento con migliaia di consegne)."""
    righe = leggi_tutte(
        lambda: supabase.table("ddt_materiali")
        .select(
            "*, articoli_fornitori(descrizione_articolo, unita_misura, codice_articolo_fornitore, "
            "fornitore_id, fornitori(ragione_sociale))"
        )
        .eq("cantiere_id", cantiere_id)
    )
    parole = [p.lower() for p in parole_ricerca(q)]

    def corrisponde(r: dict) -> bool:
        af = r.get("articoli_fornitori") or {}
        if fornitore_id and af.get("fornitore_id") != fornitore_id:
            return False
        giorno = (r.get("data_consegna") or "")[:10]
        if da and giorno < da.isoformat():
            return False
        if a and giorno > a.isoformat():
            return False
        testo = " ".join(
            str(x or "")
            for x in (
                af.get("descrizione_articolo"),
                af.get("codice_articolo_fornitore"),
                (af.get("fornitori") or {}).get("ragione_sociale"),
                r.get("numero_documento"),
            )
        ).lower()
        return all(p in testo for p in parole)

    filtrate = [r for r in righe if corrisponde(r)]
    # Dalla piu' recente; a parita' di data e ora di inserimento, per id (come la funzione SQL).
    filtrate.sort(key=lambda r: r["id"])
    filtrate.sort(key=lambda r: (r.get("data_consegna") or "", r.get("created_at") or ""), reverse=True)
    return {
        "elementi": filtrate[offset:offset + limit],
        "totale": len(filtrate),
        "importo_totale": round(sum(float(r.get("importo_totale") or 0) for r in filtrate), 2),
        "offset": offset,
        "limit": limit,
    }


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

    if data.get("articolo_fornitore_id"):
        _get_articolo_o_404(data["articolo_fornitore_id"], company_id)

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
