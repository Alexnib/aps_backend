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
    fornitore_id: str,
    risorsa_id: str,
    descrizione: str,
    unita_misura: str,
    codice: Optional[str],
    cantiere_id: Optional[str] = None,
) -> str:
    """Trova o crea la riga di prezzo per la coppia (fornitore, materiale). ``cantiere_id`` None
    identifica il prezzo "valido per tutte le commesse"; un cantiere_id specifico identifica un
    prezzo dedicato/speciale per quella sola commessa - sono due righe distinte, non si mescolano."""
    query = (
        supabase.table("articoli_fornitori")
        .select("id")
        .eq("fornitore_id", fornitore_id)
        .eq("risorsa_id", risorsa_id)
    )
    query = query.is_("cantiere_id", None) if cantiere_id is None else query.eq("cantiere_id", cantiere_id)
    esistente = query.execute()
    if esistente.data:
        return esistente.data[0]["id"]

    nuovo = (
        supabase.table("articoli_fornitori")
        .insert(
            {
                "fornitore_id": fornitore_id,
                "risorsa_id": risorsa_id,
                "cantiere_id": cantiere_id,
                "codice_articolo_fornitore": codice or None,
                "descrizione_articolo": descrizione,
                "unita_misura": unita_misura or "pz",
                "importo_unitario": 0,
            }
        )
        .execute()
    )
    return nuovo.data[0]["id"]


def trova_prezzo_articolo(fornitore_id: str, risorsa_id: str, cantiere_id: Optional[str]) -> Optional[dict]:
    """Cerca il prezzo migliore per (fornitore, materiale): preferisce un prezzo dedicato al
    cantiere indicato, altrimenti usa quello valido per tutte le commesse. Non crea nulla.
    Ritorna {id, importo_unitario, cantiere_id} o None se non e' mai stato registrato un prezzo
    per questa combinazione fornitore + materiale."""
    if cantiere_id:
        specifico = (
            supabase.table("articoli_fornitori")
            .select("id, importo_unitario, cantiere_id")
            .eq("fornitore_id", fornitore_id)
            .eq("risorsa_id", risorsa_id)
            .eq("cantiere_id", cantiere_id)
            .execute()
        )
        if specifico.data:
            return specifico.data[0]

    generale = (
        supabase.table("articoli_fornitori")
        .select("id, importo_unitario, cantiere_id")
        .eq("fornitore_id", fornitore_id)
        .eq("risorsa_id", risorsa_id)
        .is_("cantiere_id", None)
        .execute()
    )
    return generale.data[0] if generale.data else None


def trova_risorsa_per_nome(nome: str, company_id: str) -> Optional[str]:
    """Cerca (senza creare nulla) una risorsa/materiale esistente per nome esatto (case-insensitive),
    stesso criterio di corrispondenza di find_or_create_risorsa_materiale."""
    nome_norm = (nome or "").strip().lower()
    if not nome_norm:
        return None
    tipologia_id = get_tipologia_materiale_id()
    esistenti = (
        supabase.table("risorse")
        .select("id, nome_risorsa")
        .eq("company_id", company_id)
        .eq("tipologia_risorsa_id", tipologia_id)
        .execute()
    )
    for r in esistenti.data or []:
        if r["nome_risorsa"].strip().lower() == nome_norm:
            return r["id"]
    return None
