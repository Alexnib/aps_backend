import traceback
import logging
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from utils.supabase_client import supabase
from utils.auth_deps import get_current_company_id

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

        # Cantieri: i ricavi contano solo le commesse realmente attive (non i preventivi non ancora confermati)
        query_cantieri = supabase.table("cantieri").select("budget_previsto, stato, created_at").eq("company_id", company_id)
        if start_date: query_cantieri = query_cantieri.gte("created_at", start_date)
        if end_date: query_cantieri = query_cantieri.lte("created_at", end_date + "T23:59:59")
        if cantiere_id: query_cantieri = query_cantieri.eq("id", cantiere_id)
        cantieri_res = query_cantieri.execute()

        ricavi_totali = sum([
            float(c["budget_previsto"]) for c in cantieri_res.data
            if c.get("budget_previsto") and str(c.get("stato")).lower() != "preventivo"
        ])
        commesse_attive = len([c for c in cantieri_res.data if str(c.get("stato")).lower() != "chiuso"])

        costo_totale = costo_operai + costo_mezzi
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

        # Fetch Cantieri for ricavi: solo le commesse realmente attive (non i preventivi)
        query_cantieri = supabase.table("cantieri").select("budget_previsto, stato, created_at, nome_cantiere").eq("company_id", company_id)
        if start_date: query_cantieri = query_cantieri.gte("created_at", start_date)
        if end_date: query_cantieri = query_cantieri.lte("created_at", end_date + "T23:59:59")
        if cantiere_id: query_cantieri = query_cantieri.eq("id", cantiere_id)
        cantieri_res = query_cantieri.execute()

        # Build Trend Data by Day
        trend_dict = defaultdict(lambda: {"costi": 0.0, "ricavi": 0.0, "costi_operai": 0.0, "costi_mezzi": 0.0})

        # Distribution by Resource Type
        dist_dict = {"Operai": 0.0, "Mezzi": 0.0, "Materiali": 0.0}

        all_dates = set()

        for r in res_op.data:
            if r.get("data_lavoro") and r.get("importo_totale"):
                costo = float(r["importo_totale"])
                day = r["data_lavoro"][:10]
                trend_dict[day]["costi"] += costo
                trend_dict[day]["costi_operai"] += costo
                dist_dict["Operai"] += costo
                all_dates.add(day)

        for r in res_mz.data:
            if r.get("data_utilizzo") and r.get("importo_totale"):
                costo = float(r["importo_totale"])
                day = r["data_utilizzo"][:10]
                trend_dict[day]["costi"] += costo
                trend_dict[day]["costi_mezzi"] += costo
                dist_dict["Mezzi"] += costo
                all_dates.add(day)

        for c in cantieri_res.data:
            if c.get("created_at") and c.get("budget_previsto") and str(c.get("stato")).lower() != "preventivo":
                day = c["created_at"][:10]
                ricavo = float(c["budget_previsto"])
                trend_dict[day]["ricavi"] += ricavo
                all_dates.add(day)

        # Determine the date range to display
        from datetime import datetime, timedelta

        if start_date:
            start_date_obj = datetime.strptime(start_date[:10], "%Y-%m-%d")
        elif all_dates:
            start_date_obj = datetime.strptime(min(all_dates), "%Y-%m-%d")
        else:
            start_date_obj = datetime.now() - timedelta(days=30)

        if end_date:
            end_date_obj = datetime.strptime(end_date[:10], "%Y-%m-%d")
        elif all_dates:
            end_date_obj = datetime.strptime(max(all_dates), "%Y-%m-%d")
        else:
            end_date_obj = datetime.now()

        # Always generate every single day between start and end date
        sorted_dates = []
        curr = start_date_obj
        while curr <= end_date_obj:
            sorted_dates.append(curr.strftime("%Y-%m-%d"))
            curr += timedelta(days=1)

        trend_data = []

        # Cumulative tracking for Margine
        cumul_costi = 0.0
        cumul_ricavi = 0.0

        for d in sorted_dates:
            day_entry = trend_dict.get(d, {})
            day_costi = day_entry.get("costi", 0.0)
            day_ricavi = day_entry.get("ricavi", 0.0)

            cumul_costi += day_costi
            cumul_ricavi += day_ricavi

            trend_data.append({
                "date": d,
                "costi": day_costi,  # Absolute for the day (as bars)
                "ricavi": day_ricavi, # Absolute for the day (as bars)
                "costi_operai": day_entry.get("costi_operai", 0.0),
                "costi_mezzi": day_entry.get("costi_mezzi", 0.0),
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
