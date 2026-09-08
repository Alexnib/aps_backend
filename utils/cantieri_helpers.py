from datetime import datetime, timedelta
from typing import List, Optional

from utils.supabase_client import supabase


def fetch_percentuali_effettive(cantieri: List[dict]) -> dict:
    """Per ogni cantiere calcola la % di avanzamento lavori pesata sul valore delle
    voci del suo computo metrico (Produzione / Importo Contratto, come nel SAL):

        % = sum(importo_totale_voce * percentuale_completamento_voce) / sum(importo_totale_voce)

    Se il cantiere non ha nessuna voce di computo importata, la % manuale del
    cantiere (percentuale_avanzamento) resta l'unico valore disponibile (fallback).

    Ritorna un dict {cantiere_id: (percentuale_effettiva, e' derivata_dal_computo)}.
    """
    ids = [c["id"] for c in cantieri]
    risultato = {
        c["id"]: (float(c.get("percentuale_avanzamento") or 0), False) for c in cantieri
    }
    if not ids:
        return risultato

    voci_res = (
        supabase.table("computo_appalto")
        .select("cantiere_id, importo_totale, percentuale_completamento")
        .in_("cantiere_id", ids)
        .execute()
    )

    somma_importo: dict = {}
    somma_produzione: dict = {}
    for v in voci_res.data or []:
        cid = v["cantiere_id"]
        importo = float(v.get("importo_totale") or 0)
        pct = float(v.get("percentuale_completamento") or 0)
        somma_importo[cid] = somma_importo.get(cid, 0.0) + importo
        somma_produzione[cid] = somma_produzione.get(cid, 0.0) + importo * pct / 100

    for cid, tot_importo in somma_importo.items():
        if tot_importo > 0:
            risultato[cid] = ((somma_produzione[cid] / tot_importo) * 100, True)

    return risultato


def ricalcola_percentuali_correnti(cantiere_id: str) -> dict:
    """Ricalcola la percentuale "corrente" corretta di ogni voce di un cantiere: il MASSIMO valore
    mai riportato in un SAL, applicando tutti i SAL in ordine di data_sal (non di inserimento).

    Un avanzamento lavori non puo' mai regredire nella realta': se un SAL retrodatato viene
    registrato DOPO che ne esiste gia' uno con una data piu' recente, non deve "far tornare
    indietro" lo stato corrente dell'app - vince sempre il valore piu' alto mai riportato per
    quella voce, indipendentemente dall'ordine in cui i SAL sono stati inseriti.

    Ritorna {voce_id: percentuale}. Usata sia da crea_sal che da elimina_sal per tenere sempre
    coerente computo_appalto.percentuale_completamento (il valore letto ovunque nell'app).
    """
    sal_res = (
        supabase.table("sal")
        .select("data_sal, created_at, sal_voci(voce_id, percentuale_completamento)")
        .eq("cantiere_id", cantiere_id)
        .order("data_sal")
        .order("created_at")
        .execute()
    )
    correnti: dict = {}
    for s in sal_res.data or []:
        for v in s.get("sal_voci") or []:
            vid = v["voce_id"]
            pct = v["percentuale_completamento"] or 0
            if pct > correnti.get(vid, 0):
                correnti[vid] = pct
    return correnti


def calcola_intervallo_periodo(
    start_date: Optional[str],
    end_date: Optional[str],
    date_costi: set,
    cantiere_ids: List[str],
) -> List[str]:
    """Determina l'elenco dei giorni ("YYYY-MM-DD", ordine crescente) da usare per un report a
    periodo (KPI e grafico condividono questa stessa funzione, cosi' danno sempre lo stesso totale):

      - se l'utente ha scelto un intervallo esplicito (bottoni Oggi/7gg/30gg/mese/custom), lo
        rispetta cosi' com'e';
      - altrimenti ("Tutto", nessuna data indicata) lo ricava dalla prima e ultima data con un
        movimento REALE: sia i costi (rapportini/DDT, gia' raccolti dal chiamante) sia le date dei
        SAL registrati - altrimenti un cantiere con solo SAL e nessun costo ancora registrato
        sparirebbe dal periodo "Tutto" per mancanza di riferimenti temporali.
    """
    date_riferimento = set(date_costi)
    if not start_date and not end_date and cantiere_ids:
        sal_res = supabase.table("sal").select("data_sal").in_("cantiere_id", cantiere_ids).execute()
        for s in sal_res.data or []:
            if s.get("data_sal"):
                date_riferimento.add(s["data_sal"][:10])

    if start_date:
        start_obj = datetime.strptime(start_date[:10], "%Y-%m-%d")
    elif date_riferimento:
        start_obj = datetime.strptime(min(date_riferimento), "%Y-%m-%d")
    else:
        start_obj = datetime.now() - timedelta(days=30)

    if end_date:
        end_obj = datetime.strptime(end_date[:10], "%Y-%m-%d")
    elif date_riferimento:
        end_obj = datetime.strptime(max(date_riferimento), "%Y-%m-%d")
    else:
        end_obj = datetime.now()

    giorni = []
    curr = start_obj
    while curr <= end_obj:
        giorni.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)
    return giorni


def fetch_ricavi_giornalieri(cantieri: List[dict], giorni: List[str]) -> dict:
    """Ricavo GENERATO in ciascun giorno del periodo (un flusso, come i costi - non un saldo da
    ripetere su piu' giorni: sommare i valori di piu' giorni deve dare il totale del periodo, non
    un multiplo di esso):

      - cantieri con un computo metrico: il ricavo sale a scatti solo nei giorni in cui e' stato
        REALMENTE registrato un SAL, dell'importo della variazione prodotta da quel SAL rispetto al
        precedente (0 in tutti gli altri giorni, MAI negativo: per ogni voce si usa il massimo mai
        riportato, cosi' un SAL retrodatato con valori piu' bassi di uno gia' noto per una data
        successiva non fa "regredire" lo stato). Un SAL con data fuori dall'intervallo richiesto
        contribuisce comunque a calcolare correttamente lo scatto dei SAL successivi, ma il suo
        valore non viene sommato nei giorni restituiti;
      - cantieri senza computo (o senza voci con importo): non abbiamo uno storico datato per la %
        manuale, quindi il suo ricavo corrente viene distribuito in quote uguali su tutti i giorni
        del periodo (la miglior approssimazione possibile senza una data reale).

    Esclude i cantieri in stato "Preventivo" o senza budget, come nel resto della dashboard.
    Ritorna {giorno: ricavo_del_giorno}.
    """
    ricavi_per_giorno = {g: 0.0 for g in giorni}
    if not giorni:
        return ricavi_per_giorno
    giorni_set = set(giorni)

    ids = [c["id"] for c in cantieri]
    if not ids:
        return ricavi_per_giorno

    voci_res = (
        supabase.table("computo_appalto")
        .select("id, cantiere_id, importo_totale")
        .in_("cantiere_id", ids)
        .execute()
    )
    importo_per_voce: dict = {}
    totale_importo_cantiere: dict = {}
    for v in voci_res.data or []:
        importo = float(v.get("importo_totale") or 0)
        importo_per_voce[v["id"]] = importo
        totale_importo_cantiere[v["cantiere_id"]] = totale_importo_cantiere.get(v["cantiere_id"], 0.0) + importo

    cantieri_con_computo = {cid for cid, tot in totale_importo_cantiere.items() if tot > 0}

    sal_per_cantiere: dict = {}
    if cantieri_con_computo:
        sal_res = (
            supabase.table("sal")
            .select("cantiere_id, data_sal, created_at, sal_voci(voce_id, percentuale_completamento)")
            .in_("cantiere_id", list(cantieri_con_computo))
            .order("data_sal")
            .order("created_at")
            .execute()
        )
        for s in sal_res.data or []:
            pcts = {v["voce_id"]: v["percentuale_completamento"] for v in (s.get("sal_voci") or [])}
            sal_per_cantiere.setdefault(s["cantiere_id"], []).append((s["data_sal"], pcts))
        for lista in sal_per_cantiere.values():
            lista.sort(key=lambda t: t[0])

    for c in cantieri:
        if not c.get("budget_previsto") or str(c.get("stato")).lower() == "preventivo":
            continue
        cid = c["id"]
        budget = float(c["budget_previsto"])

        if cid in cantieri_con_computo:
            tot_importo = totale_importo_cantiere[cid]
            storico = sal_per_cantiere.get(cid, [])
            percentuali_correnti: dict = {}
            ricavo_precedente = 0.0
            for data_sal, pcts in storico:
                # Massimo mai riportato per ogni voce (non sovrascrittura): un SAL retrodatato con
                # valori piu' bassi di uno gia' noto per una data successiva non fa "regredire" lo
                # stato - altrimenti un SAL fuori ordine produce un balzo seguito da un crollo
                # artificiale nel grafico, anche se il totale netto tornerebbe comunque giusto.
                for vid, pct in pcts.items():
                    if pct > percentuali_correnti.get(vid, 0):
                        percentuali_correnti[vid] = pct
                produzione = sum(
                    importo_per_voce.get(vid, 0.0) * (pct / 100) for vid, pct in percentuali_correnti.items()
                )
                pct_dopo = (produzione / tot_importo) * 100 if tot_importo > 0 else 0.0
                ricavo_dopo = budget * (pct_dopo / 100)
                if data_sal in giorni_set:
                    ricavi_per_giorno[data_sal] += ricavo_dopo - ricavo_precedente
                ricavo_precedente = ricavo_dopo
        else:
            pct_manuale = float(c.get("percentuale_avanzamento") or 0)
            quota_giornaliera = (budget * (pct_manuale / 100)) / len(giorni)
            for g in giorni:
                ricavi_per_giorno[g] += quota_giornaliera

    return ricavi_per_giorno
