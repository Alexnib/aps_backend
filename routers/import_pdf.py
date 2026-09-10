import traceback
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

from models.import_pdf import (
    ConfermaComputoRequest,
    ConfermaDdtRequest,
    ConfermaPreventivoRequest,
    VoceComputoUpdate,
)
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.errors import raise_db_error
from utils.pdf_extraction import estrai_computo_metrico, estrai_ddt, estrai_preventivo_fornitore
from utils.materiali_helpers import (
    find_or_create_risorsa_materiale,
    find_or_create_articolo_fornitore,
    trova_prezzo_articolo,
    trova_risorsa_per_nome,
)

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


# ==================== PREVENTIVO FORNITORE ====================


@router.post("/preventivo/estrai")
async def estrai_preventivo(
    file: UploadFile = File(...), company_id: str = Depends(get_current_company_id)
):
    try:
        pdf_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Impossibile leggere il file caricato.")

    _valida_pdf(file, pdf_bytes)

    try:
        risultato = estrai_preventivo_fornitore(pdf_bytes, file.filename or "preventivo.pdf")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore estrazione preventivo: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Errore imprevisto durante l'estrazione del preventivo. Riprovare.",
        )

    dati = risultato["data"]
    voci = dati.get("voci", [])
    for v in voci:
        atteso = v.get("quantita", 1) * v.get("prezzo_unitario", 0)
        v["_incoerente"] = bool(v.get("importo")) and abs(atteso - v.get("importo", 0)) > 0.05

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
            supabase.table("cantieri").select("id, nome_cantiere").eq("company_id", company_id).execute()
        )
        cantieri_disponibili = cantieri_res.data or []
    except Exception as e:
        logger.error(f"Errore lettura cantieri: {str(e)}")
        cantieri_disponibili = []

    nome_fornitore = (dati.get("fornitore") or "").strip().lower()
    fornitore_suggerito = next(
        (f for f in fornitori_disponibili if f["ragione_sociale"].strip().lower() == nome_fornitore), None
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

    return {
        "fornitore": dati.get("fornitore", ""),
        "fornitore_id_suggerito": fornitore_suggerito["id"] if fornitore_suggerito else None,
        "data_offerta": dati.get("data_offerta") or "",
        "voci": voci,
        "usage": risultato["usage"],
        "fornitori_disponibili": fornitori_disponibili,
        "cantieri_disponibili": cantieri_disponibili,
    }


@router.post("/preventivo/conferma")
def conferma_preventivo(
    payload: ConfermaPreventivoRequest, company_id: str = Depends(get_current_company_id)
):
    _get_fornitore_o_404(payload.fornitore_id, company_id)
    if payload.cantiere_id:
        _get_cantiere_o_404(payload.cantiere_id, company_id)

    if not payload.voci:
        raise HTTPException(status_code=400, detail="Nessuna voce da importare.")

    try:
        importate = 0
        for v in payload.voci:
            risorsa_id = find_or_create_risorsa_materiale(v.descrizione, v.unita_misura, company_id)
            articolo_id = find_or_create_articolo_fornitore(
                payload.fornitore_id,
                risorsa_id,
                v.descrizione,
                v.unita_misura,
                v.codice_articolo,
                cantiere_id=payload.cantiere_id,
            )

            # A differenza del DDT (dove il prezzo e' un sottoprodotto della consegna), un preventivo
            # importato aggiorna SEMPRE il prezzo per questa combinazione fornitore+materiale(+cantiere):
            # e' esattamente lo scopo dell'importazione, anche per un articolo gia' noto.
            update_data = {"importo_unitario": v.prezzo_unitario, "unita_misura": v.unita_misura}
            if v.codice_articolo:
                update_data["codice_articolo_fornitore"] = v.codice_articolo
            if payload.data_offerta:
                update_data["data_offerta"] = payload.data_offerta
            supabase.table("articoli_fornitori").update(update_data).eq("id", articolo_id).execute()
            importate += 1

        return {"importate": importate}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore salvataggio preventivo: {str(e)}")
        logger.error(traceback.format_exc())
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

        # Suggerimento prezzo per ogni riga, solo lettura (non crea nulla): se il materiale e' gia'
        # in anagrafica per questo fornitore, preferisce un eventuale prezzo dedicato al cantiere
        # suggerito, altrimenti quello valido per tutte le commesse. Se non c'e' ancora nessun
        # prezzo noto, il campo resta vuoto e l'utente lo inserisce a mano nella revisione.
        for riga in doc.get("righe", []):
            prezzo_suggerito = None
            if doc["fornitore_id_suggerito"]:
                risorsa_id = trova_risorsa_per_nome(riga.get("descrizione", ""), company_id)
                if risorsa_id:
                    prezzo = trova_prezzo_articolo(
                        doc["fornitore_id_suggerito"], risorsa_id, doc["cantiere_id_suggerito"]
                    )
                    if prezzo:
                        prezzo_suggerito = prezzo["importo_unitario"]
            riga["prezzo_unitario_suggerito"] = prezzo_suggerito

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

                # Prezzo: se in anagrafica esiste gia' un prezzo per questo fornitore+materiale
                # (dedicato al cantiere della consegna, o altrimenti valido per tutte le commesse),
                # lo si usa in automatico; se l'utente lo ha corretto/inserito a mano in revisione
                # (riga.prezzo_unitario), quello vince sempre e aggiorna anche l'anagrafica per le
                # prossime volte. Un materiale mai visto prima crea un prezzo "per tutte le
                # commesse" di default (non specifico a questo cantiere).
                prezzo_esistente = trova_prezzo_articolo(doc.fornitore_id, risorsa_id, doc.cantiere_id)
                if prezzo_esistente:
                    articolo_id = prezzo_esistente["id"]
                    prezzo_unitario = (
                        riga.prezzo_unitario
                        if riga.prezzo_unitario is not None
                        else (prezzo_esistente["importo_unitario"] or 0)
                    )
                    if riga.prezzo_unitario is not None and riga.prezzo_unitario != prezzo_esistente["importo_unitario"]:
                        supabase.table("articoli_fornitori").update(
                            {"importo_unitario": riga.prezzo_unitario}
                        ).eq("id", articolo_id).execute()
                else:
                    articolo_id = find_or_create_articolo_fornitore(
                        doc.fornitore_id, risorsa_id, riga.descrizione, riga.unita_misura, riga.codice
                    )
                    prezzo_unitario = riga.prezzo_unitario if riga.prezzo_unitario is not None else 0
                    supabase.table("articoli_fornitori").update(
                        {"importo_unitario": prezzo_unitario}
                    ).eq("id", articolo_id).execute()

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
