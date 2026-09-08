import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

from models.import_pdf import ConfermaComputoRequest, ConfermaDdtRequest, VoceComputoUpdate
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.pdf_extraction import estrai_computo_metrico, estrai_ddt
from utils.materiali_helpers import find_or_create_risorsa_materiale, find_or_create_articolo_fornitore

router = APIRouter(prefix="/import", tags=["import"])

MAX_PDF_SIZE_BYTES = 32 * 1024 * 1024  # 32 MB


def _valida_pdf(file: UploadFile, pdf_bytes: bytes):
    if file.content_type not in ("application/pdf", "application/octet-stream") and not (
        file.filename or ""
    ).lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Il file caricato non e' un PDF.")
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Il file caricato e' vuoto.")
    if len(pdf_bytes) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail="Il file e' troppo grande (limite 32 MB). Suddividerlo in file piu' piccoli.",
        )


def _get_cantiere_o_404(cantiere_id: str, company_id: str) -> dict:
    res = (
        supabase.table("cantieri")
        .select("*")
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
        .select("*")
        .eq("id", fornitore_id)
        .eq("azienda_id", company_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Fornitore non trovato")
    return res.data[0]


# ==================== COMPUTO METRICO ====================


@router.post("/computo/estrai")
async def estrai_computo(
    cantiere_id: str = Form(...),
    file: UploadFile = File(...),
    company_id: str = Depends(get_current_company_id),
):
    _get_cantiere_o_404(cantiere_id, company_id)

    try:
        pdf_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Impossibile leggere il file caricato.")

    _valida_pdf(file, pdf_bytes)

    try:
        risultato = estrai_computo_metrico(pdf_bytes, file.filename or "computo.pdf")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore estrazione computo: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Errore imprevisto durante l'estrazione del computo metrico. Riprovare.",
        )

    voci = risultato["data"].get("voci", [])
    for v in voci:
        attese = v.get("quantita_prevista", 0) * v.get("importo_unitario", 0)
        v["_incoerente"] = abs(attese - v.get("importo_totale", 0)) > 0.05

    return {"voci": voci, "usage": risultato["usage"]}


@router.post("/computo/conferma")
def conferma_computo(
    payload: ConfermaComputoRequest, company_id: str = Depends(get_current_company_id)
):
    _get_cantiere_o_404(payload.cantiere_id, company_id)

    if not payload.voci:
        raise HTTPException(status_code=400, detail="Nessuna voce da importare.")

    # importo_totale e' una colonna generata dal DB (quantita_prevista * importo_unitario):
    # non va mai inclusa nell'insert, altrimenti Postgres rifiuta la scrittura.
    rows = [
        {
            "cantiere_id": payload.cantiere_id,
            "n_voce": v.n_voce,
            "codice_tariffa": v.codice_tariffa,
            "descrizione_lavorazione": v.descrizione_lavorazione,
            "unita_misura": v.unita_misura,
            "quantita_prevista": v.quantita_prevista,
            "importo_unitario": v.importo_unitario,
        }
        for v in payload.voci
    ]

    try:
        res = supabase.table("computo_appalto").insert(rows).execute()
    except Exception as e:
        logger.error(f"Errore salvataggio computo: {str(e)}")
        logger.error(traceback.format_exc())
        raise_db_error(e)

    return {"importate": len(res.data)}


@router.get("/computo/voci")
def get_voci_computo(cantiere_id: str, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(cantiere_id, company_id)
    try:
        res = (
            supabase.table("computo_appalto")
            .select("*")
            .eq("cantiere_id", cantiere_id)
            .order("created_at")
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"Errore lettura computo: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/computo/voci")
def delete_tutte_voci_computo(cantiere_id: str, company_id: str = Depends(get_current_company_id)):
    _get_cantiere_o_404(cantiere_id, company_id)
    try:
        # Lo storico SAL perde senso senza le voci a cui si riferisce: va eliminato insieme al computo.
        supabase.table("sal").delete().eq("cantiere_id", cantiere_id).execute()
        res = supabase.table("computo_appalto").delete().eq("cantiere_id", cantiere_id).execute()
        return {"message": "Computo metrico eliminato", "voci_eliminate": len(res.data)}
    except Exception as e:
        logger.error(f"Errore eliminazione completa computo: {str(e)}")
        logger.error(traceback.format_exc())
        raise_db_error(e)


def _get_voce_computo_o_404(voce_id: str, company_id: str) -> dict:
    res = (
        supabase.table("computo_appalto")
        .select("id, cantiere_id, cantieri!inner(company_id)")
        .eq("id", voce_id)
        .eq("cantieri.company_id", company_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Voce non trovata")
    return res.data[0]


@router.put("/computo/voci/{voce_id}")
def update_voce_computo(voce_id: str, payload: VoceComputoUpdate, company_id: str = Depends(get_current_company_id)):
    _get_voce_computo_o_404(voce_id, company_id)

    # importo_totale e' una colonna generata dal DB: non va mai inclusa nell'update.
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    try:
        res = supabase.table("computo_appalto").update(data).eq("id", voce_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Voce non trovata")
        return res.data[0]
    except Exception as e:
        logger.error(f"Errore aggiornamento voce computo: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)


@router.delete("/computo/voci/{voce_id}")
def delete_voce_computo(voce_id: str, company_id: str = Depends(get_current_company_id)):
    _get_voce_computo_o_404(voce_id, company_id)
    try:
        res = supabase.table("computo_appalto").delete().eq("id", voce_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Voce non trovata")
        return {"message": "Voce eliminata"}
    except Exception as e:
        logger.error(f"Errore eliminazione voce computo: {str(e)}")
        logger.error(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise e
        raise_db_error(e)


# ==================== DDT ====================


@router.post("/ddt/estrai")
async def estrai_ddt_endpoint(
    file: UploadFile = File(...), company_id: str = Depends(get_current_company_id)
):
    try:
        pdf_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Impossibile leggere il file caricato.")

    _valida_pdf(file, pdf_bytes)

    try:
        risultato = estrai_ddt(pdf_bytes, file.filename or "ddt.pdf")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore estrazione DDT: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Errore imprevisto durante l'estrazione del DDT. Riprovare.",
        )

    documenti = risultato["data"].get("documenti", [])

    try:
        fornitori_res = (
            supabase.table("fornitori").select("id, ragione_sociale").eq("azienda_id", company_id).execute()
        )
        fornitori_disponibili = fornitori_res.data or []
    except Exception as e:
        logger.error(f"Errore lettura fornitori: {str(e)}")
        fornitori_disponibili = []

    try:
        cantieri_res = (
            supabase.table("cantieri")
            .select("id, nome_cantiere, indirizzo_cantiere")
            .eq("company_id", company_id)
            .execute()
        )
        cantieri_disponibili = cantieri_res.data or []
    except Exception as e:
        logger.error(f"Errore lettura cantieri: {str(e)}")
        cantieri_disponibili = []

    for doc in documenti:
        nome_fornitore = (doc.get("fornitore") or "").strip().lower()
        fornitore_suggerito = next(
            (
                f
                for f in fornitori_disponibili
                if f["ragione_sociale"].strip().lower() == nome_fornitore
            ),
            None,
        )
        if not fornitore_suggerito and nome_fornitore:
            fornitore_suggerito = next(
                (
                    f
                    for f in fornitori_disponibili
                    if nome_fornitore in f["ragione_sociale"].strip().lower()
                    or f["ragione_sociale"].strip().lower() in nome_fornitore
                ),
                None,
            )
        doc["fornitore_id_suggerito"] = fornitore_suggerito["id"] if fornitore_suggerito else None

        # Suggerimento cantiere: solo sul nome (identificativo scelto deliberatamente, quindi
        # affidabile), non sull'indirizzo (troppo generico, causa falsi positivi). Se non c'e'
        # un match chiaro, meglio lasciare il campo vuoto e far scegliere l'utente a mano.
        destinatario = (doc.get("destinatario_testo") or "").strip().lower()
        cantiere_suggerito = None
        if destinatario:
            for c in cantieri_disponibili:
                nome = c["nome_cantiere"].strip().lower()
                if len(nome) >= 6 and nome in destinatario:
                    cantiere_suggerito = c
                    break
        doc["cantiere_id_suggerito"] = cantiere_suggerito["id"] if cantiere_suggerito else None

    return {
        "documenti": documenti,
        "usage": risultato["usage"],
        "fornitori_disponibili": fornitori_disponibili,
        "cantieri_disponibili": cantieri_disponibili,
    }


@router.post("/ddt/conferma")
def conferma_ddt(payload: ConfermaDdtRequest, company_id: str = Depends(get_current_company_id)):
    if not payload.documenti:
        raise HTTPException(status_code=400, detail="Nessun documento da importare.")

    documenti_importati = 0
    righe_importate = 0

    try:
        # Valida tutti i documenti prima di scrivere qualsiasi dato, per evitare
        # importazioni parziali se uno dei documenti fa riferimento a un cantiere
        # o fornitore non valido.
        for doc in payload.documenti:
            _get_cantiere_o_404(doc.cantiere_id, company_id)
            _get_fornitore_o_404(doc.fornitore_id, company_id)

        for doc in payload.documenti:
            if not doc.righe:
                continue

            righe_da_inserire = []
            for riga in doc.righe:
                risorsa_id = find_or_create_risorsa_materiale(
                    riga.descrizione, riga.unita_misura, company_id
                )
                articolo_id = find_or_create_articolo_fornitore(
                    doc.fornitore_id, risorsa_id, riga.descrizione, riga.unita_misura, riga.codice
                )

                prezzo_res = (
                    supabase.table("articoli_fornitori")
                    .select("importo_unitario")
                    .eq("id", articolo_id)
                    .execute()
                )
                prezzo_unitario = prezzo_res.data[0]["importo_unitario"] if prezzo_res.data else 0

                righe_da_inserire.append(
                    {
                        "cantiere_id": doc.cantiere_id,
                        "articolo_fornitore_id": articolo_id,
                        "numero_documento": doc.numero_documento,
                        "data_consegna": doc.data_ddt,
                        "quantita": riga.quantita,
                        "importo_totale": round(riga.quantita * (prezzo_unitario or 0), 2),
                    }
                )

            res = supabase.table("ddt_materiali").insert(righe_da_inserire).execute()
            documenti_importati += 1
            righe_importate += len(res.data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore salvataggio DDT: {str(e)}")
        logger.error(traceback.format_exc())
        raise_db_error(e)

    return {"documenti_importati": documenti_importati, "righe_importate": righe_importate}
