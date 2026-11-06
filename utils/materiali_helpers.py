from typing import List, Optional

from utils.supabase_client import supabase
from utils.letture import leggi_tutte


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
    righe = leggi_tutte(
        lambda: supabase.table("risorse")
        .select("id")
        .eq("company_id", company_id)
        .eq("tipologia_risorsa_id", tipologia_id)
    )
    return [r["id"] for r in righe]


def find_or_create_risorsa_materiale(nome: str, unita_misura: str, company_id: str) -> str:
    nome_norm = nome.strip()
    tipologia_id = get_tipologia_materiale_id()

    esistenti = leggi_tutte(
        lambda: supabase.table("risorse")
        .select("id, nome_risorsa")
        .eq("company_id", company_id)
        .eq("tipologia_risorsa_id", tipologia_id)
    )
    for r in esistenti:
        if r["nome_risorsa"].strip().lower() == nome_norm.lower():
            return r["id"]

    codice_risorsa = "MAT-" + "".join(ch for ch in nome_norm.upper() if ch.isalnum())[:12]
    if not codice_risorsa or codice_risorsa == "MAT-":
        codice_risorsa = f"MAT-{len(esistenti) + 1}"

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
    esistenti = leggi_tutte(
        lambda: supabase.table("risorse")
        .select("id, nome_risorsa")
        .eq("company_id", company_id)
        .eq("tipologia_risorsa_id", tipologia_id)
    )
    for r in esistenti:
        if r["nome_risorsa"].strip().lower() == nome_norm:
            return r["id"]
    return None


def _normalizza(testo: Optional[str]) -> str:
    return " ".join((testo or "").lower().split())


def cerca_prezzi_anagrafica(fornitore_id: str, cantiere_id: Optional[str], righe: List[dict]) -> List[Optional[dict]]:
    """Per ogni riga ({codice, descrizione}) cerca il prezzo gia' registrato in anagrafica per questo
    fornitore, senza creare nulla. Ritorna, nello stesso ordine, {id, importo_unitario, cantiere_id}
    oppure None.

    La corrispondenza si cerca prima per codice articolo del fornitore (il dato piu' affidabile: e'
    lo stesso codice che il fornitore stampa su preventivi e DDT), poi per descrizione, confrontata
    sia con la descrizione dell'articolo del fornitore sia con il nome del materiale. Tra piu'
    corrispondenze vince il prezzo dedicato al cantiere indicato, poi quello valido per tutte le
    commesse; un prezzo dedicato a un ALTRO cantiere non vale qui. Tutti gli articoli del fornitore
    si leggono una sola volta, non una query per riga."""
    articoli = leggi_tutte(
        lambda: supabase.table("articoli_fornitori")
        .select("id, importo_unitario, cantiere_id, codice_articolo_fornitore, descrizione_articolo, risorse(nome_risorsa)")
        .eq("fornitore_id", fornitore_id)
    )
    validi = [a for a in articoli if a.get("cantiere_id") in (None, cantiere_id)]

    def migliore(candidati: list) -> Optional[dict]:
        if not candidati:
            return None
        candidati.sort(key=lambda a: 0 if cantiere_id and a.get("cantiere_id") == cantiere_id else 1)
        a = candidati[0]
        return {"id": a["id"], "importo_unitario": a.get("importo_unitario"), "cantiere_id": a.get("cantiere_id")}

    risultati: List[Optional[dict]] = []
    for riga in righe:
        codice = _normalizza(riga.get("codice")).replace(" ", "")
        descrizione = _normalizza(riga.get("descrizione"))
        trovato = None
        if codice:
            trovato = migliore(
                [a for a in validi if _normalizza(a.get("codice_articolo_fornitore")).replace(" ", "") == codice]
            )
        if not trovato and descrizione:
            trovato = migliore(
                [
                    a
                    for a in validi
                    if _normalizza(a.get("descrizione_articolo")) == descrizione
                    or _normalizza((a.get("risorse") or {}).get("nome_risorsa")) == descrizione
                ]
            )
        risultati.append(trovato)
    return risultati
