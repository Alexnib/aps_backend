import traceback
import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from models.subappalti import AffidamentoCreate, AffidamentoUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subappalti", tags=["subappalti"])

# Le quantita' arrivano come float: senza tolleranza 10,1 + 0,2 affidati su 10,3 risulterebbero
# "oltre il disponibile" per un errore di arrotondamento.
TOLLERANZA = 1e-6

# importo_totale e' una colonna generata dal DB (quantita_prevista * importo_unitario): non va
# mai scritta. Insieme all'affidamento si leggono la ditta e la voce del computo da cui nasce, per
# confrontare quantita' e prezzo di subappalto con quelli del contratto principale.
SELECT_AFFIDAMENTO = (
    "id, cantiere_id, subappaltatore_id, voce_id, n_voce, descrizione_lavorazione, unita_misura, "
    "quantita_prevista, importo_unitario, importo_totale, created_at, "
    "subappaltatori(id, ragione_sociale), "
    "computo_appalto(id, n_voce, codice_tariffa, quantita_prevista, importo_unitario)"
)


def _get_cantiere_o_404(cantiere_id: str, company_id: str) -> dict:
    res = supabase.table("cantieri").select("id").eq("id", cantiere_id).eq("company_id", company_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Cantiere non trovato")
    return res.data[0]


def _get_ditta_o_404(subappaltatore_id: str, company_id: str) -> dict:
    res = (
        supabase.table("subappaltatori")
        .select("id, ragione_sociale")
        .eq("id", subappaltatore_id)
        .eq("azienda_id", company_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Ditta subappaltatrice non trovata")
    return res.data[0]


def _get_affidamento_o_404(affidamento_id: str, company_id: str) -> dict:
    res = (
        supabase.table("computo_subappalti")
        .select("id, cantiere_id, subappaltatore_id, voce_id, quantita_prevista, cantieri!inner(company_id)")
        .eq("id", affidamento_id)
        .eq("cantieri.company_id", company_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Affidamento non trovato")
    return res.data[0]


def _numero(n: float) -> str:
    """Quantita' in formato italiano senza decimali superflui: 12,5 / 1.250 / 3."""
    testo = f"{round(n, 3):,.3f}".rstrip("0").rstrip(".")
    return testo.replace(",", "X").replace(".", ",").replace("X", ".")


def _etichetta_voce(voce: dict) -> str:
    if voce.get("n_voce"):
        return f"voce {voce['n_voce']}"
    descrizione = (voce.get("descrizione_lavorazione") or "").strip()
    return f'"{descrizione[:50]}…"' if len(descrizione) > 50 else f'"{descrizione}"'


def _quantita_affidate(voce_ids: list[str], escludi_id: Optional[str] = None) -> dict[str, float]:
    """Quantita' gia' affidata (a qualsiasi ditta) per ciascuna voce."""
    if not voce_ids:
        return {}
    righe = (
        supabase.table("computo_subappalti")
        .select("id, voce_id, quantita_prevista")
        .in_("voce_id", voce_ids)
        .execute()
        .data
        or []
    )
    totali: dict[str, float] = defaultdict(float)
    for r in righe:
        if r["id"] != escludi_id:
            totali[r["voce_id"]] += float(r["quantita_prevista"] or 0)
    return totali


def _verifica_disponibile(voce: dict, gia_affidata: float, richiesta: float) -> None:
    disponibile = float(voce.get("quantita_prevista") or 0) - gia_affidata
    if richiesta > disponibile + TOLLERANZA:
        um = voce.get("unita_misura") or ""
        con_um = lambda n: f"{_numero(n)} {um}".strip()  # noqa: E731
        raise HTTPException(
            status_code=409,
            detail=(
                f"Per la {_etichetta_voce(voce)} restano da affidare {con_um(max(disponibile, 0))}, "
                f"ne hai indicati {con_um(richiesta)}."
            ),
        )


def _leggi_affidamenti(**filtri) -> list[dict]:
    query = supabase.table("computo_subappalti").select(SELECT_AFFIDAMENTO)
    for colonna, valore in filtri.items():
        query = query.in_(colonna, valore) if isinstance(valore, list) else query.eq(colonna, valore)
    return query.order("created_at").execute().data or []


@router.get("/")
def lista_affidamenti(cantiere_id: str, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(cantiere_id, company_id)
    try:
        return _leggi_affidamenti(cantiere_id=cantiere_id)
    except Exception as e:
        logger.error(f"Errore lettura subappalti: {traceback.format_exc()}")
        raise_db_error(e)


@router.post("/")
def crea_affidamenti(payload: AffidamentoCreate, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(payload.cantiere_id, company_id)
    _get_ditta_o_404(payload.subappaltatore_id, company_id)

    voce_ids = [v.voce_id for v in payload.voci]
    if len(set(voce_ids)) != len(voce_ids):
        raise HTTPException(status_code=400, detail="La stessa voce compare più volte nell'affidamento.")

    voci_computo = (
        supabase.table("computo_appalto")
        .select("id, n_voce, descrizione_lavorazione, unita_misura, quantita_prevista")
        .eq("cantiere_id", payload.cantiere_id)
        .in_("id", voce_ids)
        .execute()
        .data
        or []
    )
    voci_per_id = {v["id"]: v for v in voci_computo}
    if len(voci_per_id) != len(voce_ids):
        raise HTTPException(status_code=404, detail="Una o più voci non fanno parte del computo di questo cantiere.")

    gia_alla_ditta = (
        supabase.table("computo_subappalti")
        .select("voce_id")
        .eq("subappaltatore_id", payload.subappaltatore_id)
        .in_("voce_id", voce_ids)
        .execute()
        .data
        or []
    )
    if gia_alla_ditta:
        voce = voci_per_id[gia_alla_ditta[0]["voce_id"]]
        raise HTTPException(
            status_code=409,
            detail=f"La {_etichetta_voce(voce)} è già affidata a questa ditta: modifica l'affidamento esistente.",
        )

    affidate = _quantita_affidate(voce_ids)
    for v in payload.voci:
        _verifica_disponibile(voci_per_id[v.voce_id], affidate[v.voce_id], v.quantita)

    righe = [
        {
            "cantiere_id": payload.cantiere_id,
            "subappaltatore_id": payload.subappaltatore_id,
            "voce_id": v.voce_id,
            # Copia dei dati della voce: e' il contratto con la ditta e resta valido anche se il
            # computo viene modificato o reimportato in seguito.
            "n_voce": voci_per_id[v.voce_id]["n_voce"],
            "descrizione_lavorazione": voci_per_id[v.voce_id]["descrizione_lavorazione"],
            "unita_misura": voci_per_id[v.voce_id]["unita_misura"],
            "quantita_prevista": v.quantita,
            "importo_unitario": v.importo_unitario,
        }
        for v in payload.voci
    ]
    try:
        creati = supabase.table("computo_subappalti").insert(righe).execute().data or []
        return _leggi_affidamenti(id=[r["id"] for r in creati])
    except Exception as e:
        logger.error(f"Errore creazione subappalti: {traceback.format_exc()}")
        raise_db_error(e)


@router.put("/{affidamento_id}")
def aggiorna_affidamento(
    affidamento_id: str, payload: AffidamentoUpdate, company_id: str = Depends(get_current_company_id)
):
    corrente = _get_affidamento_o_404(affidamento_id, company_id)
    dati = payload.model_dump(exclude_unset=True)
    if not dati:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    if "subappaltatore_id" in dati and dati["subappaltatore_id"] != corrente["subappaltatore_id"]:
        _get_ditta_o_404(dati["subappaltatore_id"], company_id)
        if corrente["voce_id"]:
            doppione = (
                supabase.table("computo_subappalti")
                .select("id")
                .eq("subappaltatore_id", dati["subappaltatore_id"])
                .eq("voce_id", corrente["voce_id"])
                .neq("id", affidamento_id)
                .execute()
                .data
            )
            if doppione:
                raise HTTPException(status_code=409, detail="Questa voce è già affidata alla ditta scelta.")

    if "quantita" in dati:
        if corrente["voce_id"]:
            voce = (
                supabase.table("computo_appalto")
                .select("id, n_voce, descrizione_lavorazione, unita_misura, quantita_prevista")
                .eq("id", corrente["voce_id"])
                .execute()
                .data
            )
            if voce:
                altre = _quantita_affidate([corrente["voce_id"]], escludi_id=affidamento_id)
                _verifica_disponibile(voce[0], altre[corrente["voce_id"]], dati["quantita"])
        dati["quantita_prevista"] = dati.pop("quantita")

    try:
        supabase.table("computo_subappalti").update(dati).eq("id", affidamento_id).execute()
        return _leggi_affidamenti(id=affidamento_id)[0]
    except Exception as e:
        logger.error(f"Errore aggiornamento subappalto: {traceback.format_exc()}")
        raise_db_error(e)


@router.delete("/{affidamento_id}")
def elimina_affidamento(affidamento_id: str, company_id: str = Depends(get_current_company_id)):
    _get_affidamento_o_404(affidamento_id, company_id)
    try:
        supabase.table("computo_subappalti").delete().eq("id", affidamento_id).execute()
        return {"message": "Affidamento eliminato"}
    except Exception as e:
        if "23503" in str(e):
            raise HTTPException(
                status_code=409,
                detail="Questo affidamento ha già dei SAL registrati: elimina prima quelli.",
            )
        logger.error(f"Errore eliminazione subappalto: {traceback.format_exc()}")
        raise_db_error(e)
