from typing import List, Optional

from utils.supabase_client import supabase


def get_tipologia_materiale_id() -> str:
    tip_res = supabase.table("tipologie_risorse").select("id").eq("codice", "MATERIALE").execute()
    if tip_res.data:
        return tip_res.data[0]["id"]
    new_tip = (
        supabase.table("tipologie_risorse")
        .insert({"codice": "MATERIALE", "descrizione": "Materiale da cantiere"})
        .execute()
    )
    return new_tip.data[0]["id"]


def get_risorse_materiale_ids(company_id: str) -> List[str]:
    tipologia_id = get_tipologia_materiale_id()
    res = (
        supabase.table("risorse")
        .select("id")
        .eq("company_id", company_id)
        .eq("tipologia_risorsa_id", tipologia_id)
        .execute()
    )
    return [r["id"] for r in res.data or []]


def find_or_create_risorsa_materiale(nome: str, unita_misura: str, company_id: str) -> str:
    nome_norm = nome.strip()
    tipologia_id = get_tipologia_materiale_id()

    esistenti = (
        supabase.table("risorse")
        .select("id, nome_risorsa")
        .eq("company_id", company_id)
        .eq("tipologia_risorsa_id", tipologia_id)
        .execute()
    )
    for r in esistenti.data or []:
        if r["nome_risorsa"].strip().lower() == nome_norm.lower():
            return r["id"]

    codice_risorsa = "MAT-" + "".join(ch for ch in nome_norm.upper() if ch.isalnum())[:12]
    if not codice_risorsa or codice_risorsa == "MAT-":
        codice_risorsa = f"MAT-{len(esistenti.data or []) + 1}"

    nuova = (
        supabase.table("risorse")
        .insert(
            {
                "company_id": company_id,
                "tipologia_risorsa_id": tipologia_id,
                "codice_risorsa": codice_risorsa,
                "nome_risorsa": nome_norm,
                "unita_misura": unita_misura or "pz",
                "importo_unitario_standard": 0,
            }
        )
        .execute()
    )
    return nuova.data[0]["id"]


def find_or_create_articolo_fornitore(
    fornitore_id: str, risorsa_id: str, descrizione: str, unita_misura: str, codice: Optional[str]
) -> str:
    esistente = (
        supabase.table("articoli_fornitori")
        .select("id")
        .eq("fornitore_id", fornitore_id)
        .eq("risorsa_id", risorsa_id)
        .execute()
    )
    if esistente.data:
        return esistente.data[0]["id"]

    nuovo = (
        supabase.table("articoli_fornitori")
        .insert(
            {
                "fornitore_id": fornitore_id,
                "risorsa_id": risorsa_id,
                "codice_articolo_fornitore": codice or None,
                "descrizione_articolo": descrizione,
                "unita_misura": unita_misura or "pz",
                "importo_unitario": 0,
            }
        )
        .execute()
    )
    return nuovo.data[0]["id"]
