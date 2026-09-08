import traceback
import logging
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id
from utils.cantieri_helpers import fetch_ricavi_giornalieri, calcola_intervallo_periodo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@router.get("/kpi")
def get_dashboard_kpi(
    company_id: str = Depends(get_current_company_id),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    cantiere_id: Optional[str] = Query(None)
):
    try:
        # Costi totali da rapportini operai
        query_op = supabase.table("rapportini_operai").select("importo_totale, data_lavoro, operai!inner(company_id)").eq("operai.company_id", company_id)
        if start_date: query_op = query_op.gte("data_lavoro", start_date)
        if end_date: query_op = query_op.lte("data_lavoro", end_date)
        if cantiere_id: query_op = query_op.eq("cantiere_id", cantiere_id)
        res_op = query_op.execute()
        costo_operai = sum([float(r["importo_totale"]) for r in res_op.data if r.get("importo_totale")])

        # Costi totali da rapportini mezzi
        query_mz = supabase.table("rapportini_mezzi").select("importo_totale, data_utilizzo, mezzi!inner(company_id)").eq("mezzi.company_id", company_id)
        if start_date: query_mz = query_mz.gte("data_utilizzo", start_date)
        if end_date: query_mz = query_mz.lte("data_utilizzo", end_date)
        if cantiere_id: query_mz = query_mz.eq("cantiere_id", cantiere_id)
        res_mz = query_mz.execute()
        costo_mezzi = sum([float(r["importo_totale"]) for r in res_mz.data if r.get("importo_totale")])

        # Costi totali da materiali consegnati (DDT)
        query_mat = supabase.table("ddt_materiali").select("importo_totale, data_consegna, cantieri!inner(company_id)").eq("cantieri.company_id", company_id)
        if start_date: query_mat = query_mat.gte("data_consegna", start_date)
        if end_date: query_mat = query_mat.lte("data_consegna", end_date)
        if cantiere_id: query_mat = query_mat.eq("cantiere_id", cantiere_id)
        res_mat = query_mat.execute()
        costo_materiali = sum([float(r["importo_totale"]) for r in res_mat.data if r.get("importo_totale")])

        # Cantieri: i ricavi sono l'importo contrattuale scalato per la % di avanzamento lavori
        # (pesata sulle voci del computo metrico se presente, altrimenti il valore manuale del
        # cantiere), e contano solo le commesse realmente attive (non i preventivi). Il cantiere in
        # se' non si filtra per data (una commessa non "nasce e muore" nel periodo selezionato), ma
        # il RICAVO si': e' conteggiato solo per la quota maturata nel periodo (stessa logica del
        # grafico) cosi' il Margine confronta ricavi e costi dello stesso periodo, non ricavi
        # sempre-totali contro costi-solo-del-periodo.
        query_cantieri = supabase.table("cantieri").select("id, budget_previsto, stato, percentuale_avanzamento").eq("company_id", company_id)
        if cantiere_id: query_cantieri = query_cantieri.eq("id", cantiere_id)
        cantieri_res = query_cantieri.execute()

        date_costi = set()
        for r in res_op.data:
            if r.get("data_lavoro"):
                date_costi.add(r["data_lavoro"][:10])
        for r in res_mz.data:
            if r.get("data_utilizzo"):
                date_costi.add(r["data_utilizzo"][:10])
        for r in res_mat.data:
            if r.get("data_consegna"):
                date_costi.add(r["data_consegna"][:10])

        giorni = calcola_intervallo_periodo(start_date, end_date, date_costi, [c["id"] for c in cantieri_res.data])
        ricavi_totali = sum(fetch_ricavi_giornalieri(cantieri_res.data, giorni).values())
        commesse_attive = len([c for c in cantieri_res.data if str(c.get("stato")).lower() != "chiuso"])

        costo_totale = costo_operai + costo_mezzi + costo_materiali
        margine = ricavi_totali - costo_totale

        return {
            "ricavi_totali": ricavi_totali,
            "costi_totali": costo_totale,
            "margine": margine,
            "commesse_attive": commesse_attive
        }
    except Exception as e:
        print("KPI ERROR EXCEPTION:", str(e))
        logger.error(f"Error in dashboard KPI: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/charts")
def get_dashboard_charts(
    company_id: str = Depends(get_current_company_id),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    cantiere_id: Optional[str] = Query(None)
):
    try:
        # Fetch rapportini operai
        query_op = supabase.table("rapportini_operai").select("importo_totale, data_lavoro, operai!inner(company_id), cantieri(nome_cantiere)").eq("operai.company_id", company_id)
        if start_date: query_op = query_op.gte("data_lavoro", start_date)
        if end_date: query_op = query_op.lte("data_lavoro", end_date)
        if cantiere_id: query_op = query_op.eq("cantiere_id", cantiere_id)
        res_op = query_op.execute()

        # Fetch rapportini mezzi
        query_mz = supabase.table("rapportini_mezzi").select("importo_totale, data_utilizzo, mezzi!inner(company_id), cantieri(nome_cantiere)").eq("mezzi.company_id", company_id)
        if start_date: query_mz = query_mz.gte("data_utilizzo", start_date)
        if end_date: query_mz = query_mz.lte("data_utilizzo", end_date)
        if cantiere_id: query_mz = query_mz.eq("cantiere_id", cantiere_id)
        res_mz = query_mz.execute()

        # Fetch materiali consegnati (DDT)
        query_mat = supabase.table("ddt_materiali").select("importo_totale, data_consegna, cantieri!inner(company_id, nome_cantiere)").eq("cantieri.company_id", company_id)
        if start_date: query_mat = query_mat.gte("data_consegna", start_date)
        if end_date: query_mat = query_mat.lte("data_consegna", end_date)
        if cantiere_id: query_mat = query_mat.eq("cantiere_id", cantiere_id)
        res_mat = query_mat.execute()

        # Fetch Cantieri for ricavi: solo le commesse realmente attive (non i preventivi), scalati per % avanzamento.
        # Il cantiere non si filtra per data (una commessa non "nasce e muore" nel periodo), ma il
        # ricavo si': vedi calcola_intervallo_periodo/fetch_ricavi_giornalieri piu' sotto.
        query_cantieri = supabase.table("cantieri").select("id, budget_previsto, stato, nome_cantiere, percentuale_avanzamento").eq("company_id", company_id)
        if cantiere_id: query_cantieri = query_cantieri.eq("id", cantiere_id)
        cantieri_res = query_cantieri.execute()

        # Build Trend Data by Day
        trend_dict = defaultdict(lambda: {"costi": 0.0, "ricavi": 0.0, "costi_operai": 0.0, "costi_mezzi": 0.0, "costi_materiali": 0.0})

        # Distribution by Resource Type
        dist_dict = {"Operai": 0.0, "Mezzi": 0.0, "Materiali": 0.0}

        date_costi = set()

        for r in res_op.data:
            if r.get("data_lavoro") and r.get("importo_totale"):
                costo = float(r["importo_totale"])
                day = r["data_lavoro"][:10]
                trend_dict[day]["costi"] += costo
                trend_dict[day]["costi_operai"] += costo
                dist_dict["Operai"] += costo
                date_costi.add(day)

        for r in res_mz.data:
            if r.get("data_utilizzo") and r.get("importo_totale"):
                costo = float(r["importo_totale"])
                day = r["data_utilizzo"][:10]
                trend_dict[day]["costi"] += costo
                trend_dict[day]["costi_mezzi"] += costo
                dist_dict["Mezzi"] += costo
                date_costi.add(day)

        for r in res_mat.data:
            if r.get("data_consegna") and r.get("importo_totale"):
                costo = float(r["importo_totale"])
                day = r["data_consegna"][:10]
                trend_dict[day]["costi"] += costo
                trend_dict[day]["costi_materiali"] += costo
                dist_dict["Materiali"] += costo
                date_costi.add(day)

        sorted_dates = calcola_intervallo_periodo(start_date, end_date, date_costi, [c["id"] for c in cantieri_res.data])

        # Ricavo di ogni giorno ricostruito dallo storico SAL: sale solo quando in quel cantiere e'
        # stato REALMENTE registrato un SAL con quella data (o prima), non gradualmente giorno per
        # giorno - cosi' il riepilogo mensile attribuisce il ricavo al mese giusto invece di spalmarlo
        # su periodi in cui non e' stato fatto nessun sopralluogo.
        ricavi_per_giorno = fetch_ricavi_giornalieri(cantieri_res.data, sorted_dates)

        trend_data = []

        # Cumulative tracking for Margine
        cumul_costi = 0.0
        cumul_ricavi = 0.0

        for d in sorted_dates:
            day_entry = trend_dict.get(d, {})
            day_costi = day_entry.get("costi", 0.0)
            day_ricavi = ricavi_per_giorno.get(d, 0.0)

            cumul_costi += day_costi
            cumul_ricavi += day_ricavi

            trend_data.append({
                "date": d,
                "costi": day_costi,  # Absolute for the day (as bars)
                "ricavi": day_ricavi, # Absolute for the day (as bars)
                "costi_operai": day_entry.get("costi_operai", 0.0),
                "costi_mezzi": day_entry.get("costi_mezzi", 0.0),
                "costi_materiali": day_entry.get("costi_materiali", 0.0),
                "margine": cumul_ricavi - cumul_costi # Cumulative (Progressivo) for the line
            })

        # Distribution Data
        distribution_data = [{"name": k, "value": v} for k, v in dist_dict.items() if v > 0]
        # Include Materiali even if 0, so it appears in the frontend legend if not filtered
        if not any(d["name"] == "Materiali" for d in distribution_data):
            distribution_data.append({"name": "Materiali", "value": 0.0})

        # Sort by value descending
        distribution_data.sort(key=lambda x: x["value"], reverse=True)

        return {
            "trend": trend_data,
            "distribution": distribution_data
        }
    except Exception as e:
        print("CHARTS ERROR EXCEPTION:", str(e))
        logger.error(f"Error in dashboard charts: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))
