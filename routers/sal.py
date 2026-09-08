import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException

from models.sal import SalCreate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.cantieri_helpers import ricalcola_percentuali_correnti

router = APIRouter(prefix="/sal", tags=["sal"])


def _get_cantiere_o_404(cantiere_id: str, company_id: str) -> dict:
    res = supabase.table("cantieri").select("id").eq("id", cantiere_id).eq("company_id", company_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Cantiere non trovato")
    return res.data[0]


def _sal_cronologico(cantiere_id: str) -> list[dict]:
    """Tutti i SAL di un cantiere in ordine cronologico crescente, ciascuno con le sue righe (sal_voci)."""
    res = (
        supabase.table("sal")
        .select("id, data_sal, created_at, sal_voci(voce_id, percentuale_completamento)")
        .eq("cantiere_id", cantiere_id)
        .order("data_sal")
        .order("created_at")
        .execute()
    )
    return res.data or []


def _conta_modifiche_per_sal(sal_cronologico: list[dict]) -> dict[str, int]:
    """Per ogni SAL, quante voci hanno una percentuale diversa rispetto al SAL precedente (o rispetto a 0
    se la voce non compariva ancora in nessun SAL precedente)."""
    precedenti: dict[str, float] = {}
    risultato: dict[str, int] = {}
    for s in sal_cronologico:
        voci = s.get("sal_voci") or []
        modificate = 0
        for v in voci:
            vecchia = precedenti.get(v["voce_id"], 0)
            if vecchia != v["percentuale_completamento"]:
                modificate += 1
        risultato[s["id"]] = modificate
        for v in voci:
            precedenti[v["voce_id"]] = v["percentuale_completamento"]
    return risultato


@router.post("/")
def crea_sal(payload: SalCreate, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(payload.cantiere_id, company_id)

    if not payload.percentuali:
        raise HTTPException(status_code=400, detail="Nessuna percentuale da registrare.")

    # Verifica che tutte le voci indicate appartengano davvero a questo cantiere,
    # cosi' un SAL non puo' mai toccare dati di un'altra commessa. La stessa query
    # ci da' anche la percentuale "corrente" (= ultimo SAL, o 0) per capire dopo
    # quante voci sono state DAVVERO modificate rispetto a prima.
    voce_ids = [p.voce_id for p in payload.percentuali]
    voci_res = (
        supabase.table("computo_appalto")
        .select("id, percentuale_completamento")
        .eq("cantiere_id", payload.cantiere_id)
        .in_("id", voce_ids)
        .execute()
    )
    percentuali_precedenti = {v["id"]: (v.get("percentuale_completamento") or 0) for v in voci_res.data or []}
    if len(percentuali_precedenti) != len(set(voce_ids)):
        raise HTTPException(status_code=400, detail="Una o piu' voci non appartengono a questo cantiere.")

    try:
        sal_res = (
            supabase.table("sal")
            .insert({"cantiere_id": payload.cantiere_id, "data_sal": payload.data_sal})
            .execute()
        )
        sal_id = sal_res.data[0]["id"]

        righe = [
            {"sal_id": sal_id, "voce_id": p.voce_id, "percentuale_completamento": p.percentuale_completamento}
            for p in payload.percentuali
        ]
        supabase.table("sal_voci").insert(righe).execute()

        # Aggiorna il valore "corrente" su ogni voce: e' quello letto ovunque nell'app (media
        # pesata, dashboard) senza dover rifare il join con lo storico SAL ad ogni richiesta.
        # NB: non si scrive semplicemente il valore appena inviato - si ricalcola il massimo mai
        # riportato per quella voce in ordine di data_sal, cosi' un SAL retrodatato (con data nel
        # passato registrata DOPO che ne esiste gia' uno piu' recente) non fa "regredire" lo stato
        # corrente dell'app ai suoi valori, che potrebbero essere piu' bassi.
        percentuali_ricalcolate = ricalcola_percentuali_correnti(payload.cantiere_id)
        for p in payload.percentuali:
            nuovo_valore = percentuali_ricalcolate.get(p.voce_id, 0)
            supabase.table("computo_appalto").update(
                {"percentuale_completamento": nuovo_valore}
            ).eq("id", p.voce_id).execute()

        voci_modificate = sum(
            1
            for p in payload.percentuali
            if percentuali_precedenti.get(p.voce_id, 0) != percentuali_ricalcolate.get(p.voce_id, 0)
        )

        return {
            "id": sal_id,
            "data_sal": payload.data_sal,
            "voci_aggiornate": len(righe),
            "voci_modificate": voci_modificate,
        }
    except Exception as e:
        logger.error(f"Errore creazione SAL: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)


@router.get("/")
def lista_sal(cantiere_id: str, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(cantiere_id, company_id)
    try:
        cronologico = _sal_cronologico(cantiere_id)
        modifiche = _conta_modifiche_per_sal(cronologico)
        ordinato = sorted(cronologico, key=lambda s: (s["data_sal"], s["created_at"]), reverse=True)
        return [
            {
                "id": s["id"],
                "data_sal": s["data_sal"],
                "created_at": s["created_at"],
                "numero_voci": len(s.get("sal_voci") or []),
                "voci_modificate": modifiche.get(s["id"], 0),
            }
            for s in ordinato
        ]
    except Exception as e:
        logger.error(f"Errore lettura elenco SAL: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{sal_id}")
def dettaglio_sal(sal_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        sal_res = (
            supabase.table("sal")
            .select("id, cantiere_id, data_sal, created_at, cantieri!inner(company_id)")
            .eq("id", sal_id)
            .eq("cantieri.company_id", company_id)
            .execute()
        )
        if not sal_res.data:
            raise HTTPException(status_code=404, detail="SAL non trovato")
        sal = sal_res.data[0]

        # Percentuale di ogni voce cosi' com'era PRIMA di questo SAL (in base ai SAL precedenti,
        # o 0 se la voce non era ancora mai stata registrata), per poter segnalare cosa e' cambiato.
        precedenti: dict[str, float] = {}
        for s in _sal_cronologico(sal["cantiere_id"]):
            if s["id"] == sal_id:
                break
            for v in s.get("sal_voci") or []:
                precedenti[v["voce_id"]] = v["percentuale_completamento"]

        voci_res = (
            supabase.table("sal_voci")
            .select("id, voce_id, percentuale_completamento, computo_appalto(n_voce, descrizione_lavorazione, codice_tariffa)")
            .eq("sal_id", sal_id)
            .execute()
        )
        voci = []
        for v in voci_res.data or []:
            precedente = precedenti.get(v["voce_id"], 0)
            voci.append(
                {
                    **v,
                    "percentuale_precedente": precedente,
                    "modificata": precedente != v["percentuale_completamento"],
                }
            )

        return {
            "id": sal["id"],
            "cantiere_id": sal["cantiere_id"],
            "data_sal": sal["data_sal"],
            "created_at": sal["created_at"],
            "voci": voci,
        }
    except Exception as e:
        logger.error(f"Errore lettura dettaglio SAL: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{sal_id}")
def elimina_sal(sal_id: str, company_id: str = Depends(get_current_company_id)):
    try:
        sal_res = (
            supabase.table("sal")
            .select("id, cantiere_id, cantieri!inner(company_id)")
            .eq("id", sal_id)
            .eq("cantieri.company_id", company_id)
            .execute()
        )
        if not sal_res.data:
            raise HTTPException(status_code=404, detail="SAL non trovato")
        cantiere_id = sal_res.data[0]["cantiere_id"]

        ultimo_res = (
            supabase.table("sal")
            .select("id")
            .eq("cantiere_id", cantiere_id)
            .order("data_sal", desc=True)
            .limit(1)
            .execute()
        )
        if not ultimo_res.data or ultimo_res.data[0]["id"] != sal_id:
            raise HTTPException(
                status_code=400,
                detail="Puoi eliminare solo l'ultimo SAL registrato per questo cantiere, per non alterare lo storico.",
            )

        voci_res = supabase.table("sal_voci").select("voce_id").eq("sal_id", sal_id).execute()
        voce_ids = [v["voce_id"] for v in voci_res.data or []]

        supabase.table("sal").delete().eq("id", sal_id).execute()

        # Ricalcola la percentuale "corrente" delle voci coinvolte usando i SAL rimasti (il massimo
        # mai riportato per data_sal, non semplicemente il SAL che ora risulta piu' recente): se
        # esisteva anche un altro SAL con data anteriore ma valori piu' alti per la stessa voce
        # (caso di SAL retrodatati), quel valore resta quello corretto anche dopo la cancellazione.
        if voce_ids:
            percentuali_ricalcolate = ricalcola_percentuali_correnti(cantiere_id)
            for vid in voce_ids:
                nuovo_valore = percentuali_ricalcolate.get(vid, 0)
                supabase.table("computo_appalto").update({"percentuale_completamento": nuovo_valore}).eq(
                    "id", vid
                ).execute()

        return {"message": "SAL eliminato"}
    except Exception as e:
        logger.error(f"Errore eliminazione SAL: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)
