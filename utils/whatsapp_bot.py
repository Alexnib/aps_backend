"""Assistente WhatsApp di APS Light.

Dai numeri collegati a un account (Profilo > WhatsApp) accetta messaggi scritti e vocali e
permette SOLO di: vedere mezzi e operai, consultare i rapportini, registrarli, modificarli o
eliminarli. Claude interpreta la richiesta usando un insieme chiuso di strumenti, tutti limitati
all'azienda del numero. Un rapportino non viene mai salvato senza la conferma dell'utente: il
riepilogo arriva con i pulsanti "Conferma" / "Annulla".

Configurazione: vedi utils/whatsapp_api.py e utils/trascrizione.py, piu'
  WHATSAPP_CLAUDE_MODEL   (default claude-haiku-5-5)
  WHATSAPP_CLAUDE_EFFORT  (default medium)
"""
import hashlib
import json
import logging
import os
import threading
import traceback
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from utils.anthropic_client import get_anthropic_client
from utils.auth_deps import MESSAGGI_STATO, Profilo, carica_profilo
from utils.letture import leggi_tutte
from utils.supabase_client import supabase
from utils.trascrizione import TrascrizioneNonRiuscita, trascrivi
from utils.whatsapp_api import invia_pulsanti, invia_testo, scarica_media, segna_letto

logger = logging.getLogger(__name__)

MODELLO = os.environ.get("WHATSAPP_CLAUDE_MODEL", "claude-haiku-5-5")
IMPEGNO = os.environ.get("WHATSAPP_CLAUDE_EFFORT", "medium")
# Modelli che accettano il ripiego automatico su un altro modello in caso di rifiuto.
_MODELLI_CON_RIPIEGO = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

FUSO = ZoneInfo("Europe/Rome")
# Dopo questa pausa la conversazione riparte da zero (e un riepilogo non confermato scade).
DURATA_SESSIONE = timedelta(minutes=30)
# Oltre questo numero di messaggi si riparte comunque: la cronologia non si accorcia mai a meta'.
MAX_MESSAGGI_SESSIONE = 80
# Passaggi massimi (chiamate a Claude) per rispondere a un singolo messaggio.
MAX_PASSAGGI = 8
MAX_GIORNI_RICERCA = 366
MAX_GIORNATE_MOSTRATE = 40

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

SYSTEM_PROMPT = """Sei l'assistente WhatsApp di APS Light, il gestionale di un'impresa edile. Parli in italiano con una persona dell'impresa (titolare, capocantiere...) dal suo telefono.

Puoi fare solo queste cose:
- mostrare i mezzi e gli operai dell'impresa;
- consultare i rapportini (per giorno, periodo, cantiere, operaio o mezzo, con ore e importi);
- registrare un rapportino, modificarlo o eliminarlo.
Per qualsiasi altra richiesta rispondi in una frase che puoi aiutare solo con mezzi, operai e rapportini.

Un rapportino è una giornata di lavoro su un cantiere: per ogni operaio e per ogni mezzo, le ore. Gli importi li calcola il gestionale dai costi orari.

Come lavorare:
- Ogni messaggio dell'utente comincia con giorno, data e ora attuali tra parentesi quadre: "oggi", "ieri", "lunedì scorso" si contano da lì. Se per un rapportino l'utente non dice il giorno, intende oggi. Non si registrano rapportini per giorni futuri.
- Gli ID servono solo agli strumenti: prendili con elenco_cantieri, elenco_operai, elenco_mezzi e non mostrarli mai all'utente. Abbina i nomi detti dall'utente a quelli in elenco (anche solo il cognome, un soprannome o un nome storpiato dalla trascrizione di un vocale). Se un nome può corrispondere a più persone o mezzi, o non trovi nulla di simile, chiedi invece di indovinare. Se manca il cantiere e ce n'è più d'uno, chiedilo.
- Per registrare o modificare usa prepara_rapportino con la composizione COMPLETA della giornata su quel cantiere, cioè tutti gli operai e i mezzi che devono risultare alla fine. Per modificare un rapportino esistente leggilo prima con cerca_rapportini, poi cambia solo ciò che chiede l'utente. Per eliminarlo usa prepara_rapportino con entrambi gli elenchi vuoti.
- prepara_rapportino non salva nulla: il riepilogo arriva all'utente con i pulsanti Conferma e Annulla. Usa conferma_rapportino solo se l'utente conferma a parole un riepilogo che ha già ricevuto (es. "sì", "confermo", "va bene"); se rinuncia, usa scarta_bozza.
- Le domande sulle ore e sui costi si rispondono con cerca_rapportini: i totali che restituisce sono già calcolati, non rifare i conti.

Stile delle risposte: brevi, come in una chat. Non usare emoji. Niente tabelle né titoli markdown: per il grassetto usa *un asterisco*, per gli elenchi •. Ore come "8 h", importi come "€ 1.234,50", date come "lunedì 03/11"."""


def _strumento(nome: str, descrizione: str, proprieta: dict) -> dict:
    return {
        "name": nome,
        "description": descrizione,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": proprieta,
            "required": list(proprieta),
            "additionalProperties": False,
        },
    }


def _righe_ore(chiave_id: str, cosa: str) -> dict:
    return {
        "type": "array",
        "description": f"Tutti i {cosa} della giornata con le ore (vuoto se nessuno).",
        "items": {
            "type": "object",
            "properties": {chiave_id: {"type": "string"}, "ore": {"type": "number", "description": "Ore lavorate, da 0,5 a 24."}},
            "required": [chiave_id, "ore"],
            "additionalProperties": False,
        },
    }


_ID_FACOLTATIVO = {"type": ["string", "null"]}

STRUMENTI = [
    _strumento("elenco_cantieri", "Cantieri dell'impresa con ID, codice, nome e stato.", {}),
    _strumento("elenco_operai", "Operai dell'impresa con ID, nome, matricola e costo orario.", {}),
    _strumento("elenco_mezzi", "Mezzi dell'impresa con ID, targa o matricola, descrizione e costo orario.", {}),
    _strumento(
        "cerca_rapportini",
        "Rapportini in un periodo (estremi inclusi), dal più recente, con ore e importi per operaio e mezzo e i "
        "totali del periodo. Con operaio_id o mezzo_id contano solo le ore di quell'operaio o mezzo.",
        {
            "da": {"type": "string", "description": "Primo giorno, formato AAAA-MM-GG."},
            "a": {"type": "string", "description": "Ultimo giorno, formato AAAA-MM-GG."},
            "cantiere_id": _ID_FACOLTATIVO,
            "operaio_id": _ID_FACOLTATIVO,
            "mezzo_id": _ID_FACOLTATIVO,
        },
    ),
    _strumento(
        "prepara_rapportino",
        "Prepara (senza salvare) il rapportino di un giorno su un cantiere con la composizione completa indicata: "
        "crea, modifica o, con elenchi vuoti, elimina. All'utente arriva il riepilogo con i pulsanti Conferma/Annulla.",
        {
            "data": {"type": "string", "description": "Giorno, formato AAAA-MM-GG."},
            "cantiere_id": {"type": "string"},
            "operai": _righe_ore("operaio_id", "operai"),
            "mezzi": _righe_ore("mezzo_id", "mezzi"),
        },
    ),
    _strumento("conferma_rapportino", "Salva il rapportino preparato, dopo che l'utente l'ha confermato a parole.", {}),
    _strumento("scarta_bozza", "Scarta il rapportino preparato e non ancora confermato.", {}),
]

# Impronta di modello, istruzioni e strumenti. A meta' conversazione non si possono cambiare (il
# ragionamento gia' fatto da Claude non varrebbe piu'): dopo un aggiornamento del codice le
# conversazioni salvate con un'impronta diversa ripartono da zero.
VERSIONE = hashlib.sha256(
    (MODELLO + SYSTEM_PROMPT + json.dumps(STRUMENTI, sort_keys=True)).encode()
).hexdigest()[:12]


# ------------------------------------------------------------------ formattazione

def _euro(valore: float) -> str:
    testo = f"{valore:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {testo}"


def _ore(valore: float) -> str:
    return f"{float(valore):g}".replace(".", ",") + " h"


def _giorno(giorno: date) -> str:
    return f"{GIORNI[giorno.weekday()]} {giorno:%d/%m/%Y}"


def _oggi() -> date:
    return datetime.now(FUSO).date()


# ------------------------------------------------------------------ anagrafiche dell'azienda

def _costo(riga: dict) -> float:
    return float((riga.get("risorse") or {}).get("importo_unitario_standard") or 0)


def _operai(company_id: str) -> list[dict]:
    return leggi_tutte(
        lambda: supabase.table("operai")
        .select("id, nome, cognome, matricola, risorse(importo_unitario_standard)")
        .eq("company_id", company_id)
        .order("cognome")
        .order("nome")
    )


def _mezzi(company_id: str) -> list[dict]:
    return leggi_tutte(
        lambda: supabase.table("mezzi")
        .select("id, targa_matricola, descrizione, risorse(importo_unitario_standard)")
        .eq("company_id", company_id)
        .order("targa_matricola")
    )


def _cantieri(company_id: str) -> list[dict]:
    return leggi_tutte(
        lambda: supabase.table("cantieri")
        .select("id, codice_interno, nome_cantiere, stato")
        .eq("company_id", company_id)
        .order("nome_cantiere")
    )


def _nome_operaio(o: dict) -> str:
    return f"{o.get('nome') or ''} {o.get('cognome') or ''}".strip()


def _nome_mezzo(m: dict) -> str:
    return f"{m.get('targa_matricola')} ({m['descrizione']})" if m.get("descrizione") else m.get("targa_matricola") or "-"


def _suggerimento_trascrizione(company_id: str) -> str:
    """Nomi propri dell'azienda, perche' Whisper li scriva giusti."""
    nomi = [c["nome_cantiere"] for c in _cantieri(company_id)]
    nomi += [_nome_operaio(o) for o in _operai(company_id)]
    nomi += [m.get("descrizione") or m["targa_matricola"] for m in _mezzi(company_id)]
    return "Rapportino di cantiere. Nomi: " + ", ".join(n for n in nomi if n)


# ------------------------------------------------------------------ rapportini: lettura, piano, salvataggio

def _righe_giornata(company_id: str, giorno: str, cantiere_id: str) -> tuple[list[dict], list[dict]]:
    operai = (
        supabase.table("rapportini_operai")
        .select("id, operaio_id, quantita_ore, operai!inner(company_id)")
        .eq("operai.company_id", company_id)
        .eq("data_lavoro", giorno)
        .eq("cantiere_id", cantiere_id)
        .execute()
        .data
    )
    mezzi = (
        supabase.table("rapportini_mezzi")
        .select("id, mezzo_id, quantita_ore, mezzi!inner(company_id)")
        .eq("mezzi.company_id", company_id)
        .eq("data_utilizzo", giorno)
        .eq("cantiere_id", cantiere_id)
        .execute()
        .data
    )
    return operai, mezzi


def _piano(esistenti: list[dict], colonna_id: str, obiettivo: dict[str, float]) -> list[dict]:
    """Cosa cambiare per passare dalle righe salvate alla composizione voluta, risorsa per risorsa."""
    per_risorsa: dict[str, list[dict]] = defaultdict(list)
    for riga in esistenti:
        per_risorsa[riga[colonna_id]].append(riga)
    passi = []
    for risorsa_id, ore in obiettivo.items():
        righe = per_risorsa.pop(risorsa_id, [])
        prima = sum(float(r["quantita_ore"] or 0) for r in righe)
        if not righe:
            azione = "nuovo"
        elif len(righe) == 1 and abs(prima - ore) < 1e-9:
            azione = "invariato"
        else:
            azione = "modifica"
        passi.append({"azione": azione, "risorsa_id": risorsa_id, "ore": ore, "prima": prima, "righe": [r["id"] for r in righe]})
    for risorsa_id, righe in per_risorsa.items():
        prima = sum(float(r["quantita_ore"] or 0) for r in righe)
        passi.append({"azione": "togli", "risorsa_id": risorsa_id, "ore": 0, "prima": prima, "righe": [r["id"] for r in righe]})
    return passi


def _riepilogo(bozza: dict, passi_operai: list[dict], passi_mezzi: list[dict], nomi: dict[str, str], costi: dict[str, float]) -> str:
    giorno = date.fromisoformat(bozza["data"])
    esisteva = any(p["azione"] != "nuovo" for p in passi_operai + passi_mezzi)
    elimina = not bozza["operai"] and not bozza["mezzi"]
    if elimina:
        titolo = "*Eliminare questo rapportino?*"
    elif esisteva:
        titolo = "*Modifica del rapportino*"
    else:
        titolo = "*Nuovo rapportino*"
    righe = [titolo, f"{_giorno(giorno).capitalize()} · {bozza['cantiere_nome']}"]

    def sezione(intestazione: str, passi: list[dict]):
        if not passi:
            return
        righe.append("")
        righe.append(intestazione)
        for p in sorted(passi, key=lambda p: nomi.get(p["risorsa_id"], "").lower()):
            nome = nomi.get(p["risorsa_id"], "?")
            if p["azione"] == "togli":
                righe.append(f"• ~{nome} — {_ore(p['prima'])}~ (tolto)")
            elif p["azione"] == "modifica":
                righe.append(f"• {nome} — {_ore(p['ore'])} (prima {_ore(p['prima'])})")
            elif p["azione"] == "nuovo" and esisteva:
                righe.append(f"• {nome} — {_ore(p['ore'])} (aggiunto)")
            else:
                righe.append(f"• {nome} — {_ore(p['ore'])}")

    sezione("*Operai*", passi_operai)
    sezione("*Mezzi*", passi_mezzi)
    if not elimina:
        tutte = [(rid, ore) for rid, ore in {**bozza["operai"], **bozza["mezzi"]}.items()]
        ore_totali = sum(ore for _, ore in tutte)
        importo = sum(ore * costi.get(rid, 0) for rid, ore in tutte)
        righe.append("")
        righe.append(f"*Totale:* {_ore(ore_totali)} · {_euro(importo)}")
    return "\n".join(righe)


def _bozza_scaduta(bozza: dict) -> bool:
    try:
        creata = datetime.fromisoformat(bozza["creata_at"])
    except (KeyError, ValueError):
        return True
    return datetime.now(FUSO) - creata > DURATA_SESSIONE


def _applica_bozza(company_id: str, bozza: dict) -> str:
    """Salva il rapportino della bozza, ripartendo dallo stato attuale del database (se nel frattempo
    qualcuno l'ha cambiato dal gestionale, si applica comunque la composizione confermata)."""
    giorno, cantiere_id = bozza["data"], bozza["cantiere_id"]
    righe_operai, righe_mezzi = _righe_giornata(company_id, giorno, cantiere_id)
    costi = {o["id"]: _costo(o) for o in _operai(company_id)} | {m["id"]: _costo(m) for m in _mezzi(company_id)}

    for tabella, colonna_id, colonna_data, esistenti, obiettivo in (
        ("rapportini_operai", "operaio_id", "data_lavoro", righe_operai, bozza["operai"]),
        ("rapportini_mezzi", "mezzo_id", "data_utilizzo", righe_mezzi, bozza["mezzi"]),
    ):
        nuove = []
        for passo in _piano(esistenti, colonna_id, obiettivo):
            importo = round(passo["ore"] * costi.get(passo["risorsa_id"], 0), 2)
            if passo["azione"] == "nuovo":
                nuove.append({
                    "cantiere_id": cantiere_id,
                    colonna_id: passo["risorsa_id"],
                    colonna_data: giorno,
                    "quantita_ore": passo["ore"],
                    "importo_totale": importo,
                })
            elif passo["azione"] == "modifica":
                prima, *doppie = passo["righe"]
                supabase.table(tabella).update({"quantita_ore": passo["ore"], "importo_totale": importo}).eq("id", prima).execute()
                if doppie:
                    supabase.table(tabella).delete().in_("id", doppie).execute()
            elif passo["azione"] == "togli":
                supabase.table(tabella).delete().in_("id", passo["righe"]).execute()
        if nuove:
            supabase.table(tabella).insert(nuove).execute()

    giorno_letto = _giorno(date.fromisoformat(giorno))
    if not bozza["operai"] and not bozza["mezzi"]:
        return f"Rapportino di {giorno_letto} a {bozza['cantiere_nome']} eliminato."
    ore = sum(bozza["operai"].values()) + sum(bozza["mezzi"].values())
    return f"Rapportino di {giorno_letto} a {bozza['cantiere_nome']} salvato ({_ore(ore)})."


# ------------------------------------------------------------------ strumenti di Claude

class ErroreStrumento(Exception):
    """Richiesta non valida: il messaggio torna a Claude, che spiega o corregge."""


class Strumenti:
    """Gli strumenti di Claude per una conversazione: tutti limitati all'azienda `company_id`."""

    def __init__(self, company_id: str, stato: dict):
        self.company_id = company_id
        # stato["bozza"]: rapportino in attesa di conferma; stato["bozza_nuova"]: preparato in
        # questo messaggio (da mostrare con i pulsanti); stato["salvato"]: esito del salvataggio.
        self.stato = stato

    def esegui(self, nome: str, dati: dict) -> tuple[str, bool]:
        try:
            metodo = getattr(self, f"_{nome}", None)
            if metodo is None:
                raise ErroreStrumento(f"Strumento sconosciuto: {nome}")
            return metodo(**dati), False
        except ErroreStrumento as e:
            return str(e), True
        except Exception:
            logger.error(f"WhatsApp: errore nello strumento {nome}: {traceback.format_exc()}")
            return "Errore interno del gestionale: riprova o chiedi all'utente di riprovare più tardi.", True

    # --- consultazione

    def _elenco_cantieri(self) -> str:
        cantieri = _cantieri(self.company_id)
        if not cantieri:
            return "Nessun cantiere."
        return "\n".join(f"- [{c['id']}] {c['nome_cantiere']} · codice {c['codice_interno']} · {c['stato']}" for c in cantieri)

    def _elenco_operai(self) -> str:
        operai = _operai(self.company_id)
        if not operai:
            return "Nessun operaio."
        return "\n".join(
            f"- [{o['id']}] {_nome_operaio(o)}" + (f" · matricola {o['matricola']}" if o.get("matricola") else "")
            + f" · {_euro(_costo(o))}/h"
            for o in operai
        )

    def _elenco_mezzi(self) -> str:
        mezzi = _mezzi(self.company_id)
        if not mezzi:
            return "Nessun mezzo."
        return "\n".join(f"- [{m['id']}] {_nome_mezzo(m)} · {_euro(_costo(m))}/h" for m in mezzi)

    def _cerca_rapportini(self, da: str, a: str, cantiere_id: Optional[str], operaio_id: Optional[str], mezzo_id: Optional[str]) -> str:
        from routers.rapportini import elenco_rapportini

        inizio, fine = _data(da), _data(a)
        if inizio > fine:
            inizio, fine = fine, inizio
        if (fine - inizio).days > MAX_GIORNI_RICERCA:
            raise ErroreStrumento(f"Periodo troppo lungo: al massimo {MAX_GIORNI_RICERCA} giorni.")
        risultato = elenco_rapportini(
            da=inizio, a=fine, cantiere_id=cantiere_id or None, operaio_id=operaio_id or None,
            mezzo_id=mezzo_id or None, offset=0, limit=MAX_GIORNATE_MOSTRATE, company_id=self.company_id,
        )
        totale = risultato["totale"]
        righe = [
            f"{totale} rapportini dal {inizio:%d/%m/%Y} al {fine:%d/%m/%Y} · totale {_ore(risultato['ore_totali'])} · "
            f"{_euro(risultato['importo_totale'])}"
        ]
        for r in risultato["rapportini"]:
            operai = ", ".join(f"{o['nome']} {_ore(o['ore'])}" for o in r["operai"]) or "nessuno"
            mezzi = ", ".join(f"{m['nome']} {_ore(m['ore'])}" for m in r["mezzi"]) or "nessuno"
            righe.append(
                f"- {_giorno(date.fromisoformat(r['data'][:10]))} · {r['cantiere_nome']} [cantiere {r['cantiere_id']}]: "
                f"operai {operai}; mezzi {mezzi} · {_ore(r['ore_totali'])} · {_euro(r['importo_totale'])}"
            )
        if totale > len(risultato["rapportini"]):
            righe.append(f"(mostrati i {len(risultato['rapportini'])} più recenti; i totali valgono per tutti)")
        return "\n".join(righe)

    # --- registrazione

    def _prepara_rapportino(self, data: str, cantiere_id: str, operai: list[dict], mezzi: list[dict]) -> str:
        giorno = _data(data)
        if giorno > _oggi():
            raise ErroreStrumento("Non si possono registrare rapportini per giorni futuri: controlla la data con l'utente.")
        cantiere = next((c for c in _cantieri(self.company_id) if c["id"] == cantiere_id), None)
        if not cantiere:
            raise ErroreStrumento("Cantiere non trovato tra quelli dell'impresa: usa elenco_cantieri.")

        anagrafica_operai = {o["id"]: o for o in _operai(self.company_id)}
        anagrafica_mezzi = {m["id"]: m for m in _mezzi(self.company_id)}
        voluti_operai = _ore_per_risorsa(operai, "operaio_id", anagrafica_operai, "operaio")
        voluti_mezzi = _ore_per_risorsa(mezzi, "mezzo_id", anagrafica_mezzi, "mezzo")

        righe_operai, righe_mezzi = _righe_giornata(self.company_id, giorno.isoformat(), cantiere_id)
        passi_operai = _piano(righe_operai, "operaio_id", voluti_operai)
        passi_mezzi = _piano(righe_mezzi, "mezzo_id", voluti_mezzi)
        if not voluti_operai and not voluti_mezzi and not righe_operai and not righe_mezzi:
            raise ErroreStrumento("Per quel giorno e cantiere non c'è nessun rapportino da eliminare.")
        if all(p["azione"] == "invariato" for p in passi_operai + passi_mezzi):
            return "Il rapportino è già esattamente così: non c'è nulla da salvare. Dillo all'utente."

        bozza = {
            "id": uuid.uuid4().hex[:12],
            "data": giorno.isoformat(),
            "cantiere_id": cantiere_id,
            "cantiere_nome": cantiere["nome_cantiere"],
            "operai": voluti_operai,
            "mezzi": voluti_mezzi,
            "creata_at": datetime.now(FUSO).isoformat(),
        }
        # Per il riepilogo servono i nomi anche delle righe tolte (risorse non piu' nella bozza).
        nomi = {rid: _nome_operaio(o) for rid, o in anagrafica_operai.items()} | {rid: _nome_mezzo(m) for rid, m in anagrafica_mezzi.items()}
        costi = {rid: _costo(o) for rid, o in anagrafica_operai.items()} | {rid: _costo(m) for rid, m in anagrafica_mezzi.items()}
        bozza["riepilogo"] = _riepilogo(bozza, passi_operai, passi_mezzi, nomi, costi)
        self.stato["bozza"] = bozza
        self.stato["bozza_nuova"] = True
        return (
            "Bozza pronta, NON ancora salvata. L'utente riceverà questo riepilogo con i pulsanti Conferma e Annulla:\n"
            f"{bozza['riepilogo']}\n"
            "Non ripetere il riepilogo: rispondi con al massimo una frase breve (o nulla)."
        )

    def _conferma_rapportino(self) -> str:
        bozza = self.stato.get("bozza")
        if not bozza or _bozza_scaduta(bozza):
            raise ErroreStrumento("Non c'è nessun rapportino in attesa di conferma (o è scaduto): preparalo di nuovo.")
        if self.stato.get("bozza_nuova"):
            raise ErroreStrumento("L'utente non ha ancora visto il riepilogo: aspetta la sua conferma.")
        esito = _applica_bozza(self.company_id, bozza)
        self.stato["bozza"] = None
        self.stato["salvato"] = esito
        return esito

    def _scarta_bozza(self) -> str:
        self.stato["bozza"] = None
        return "Bozza scartata: non è stato salvato nulla."


def _data(testo: str) -> date:
    try:
        return date.fromisoformat((testo or "").strip()[:10])
    except ValueError:
        raise ErroreStrumento(f"Data non valida: {testo!r}. Usa il formato AAAA-MM-GG.")


def _ore_per_risorsa(righe: list[dict], chiave: str, anagrafica: dict, cosa: str) -> dict[str, float]:
    risultato: dict[str, float] = {}
    for riga in righe or []:
        risorsa_id = (riga.get(chiave) or "").strip()
        if risorsa_id not in anagrafica:
            raise ErroreStrumento(f"{cosa.capitalize()} non trovato tra quelli dell'impresa (ID {risorsa_id!r}): usa l'elenco.")
        if risorsa_id in risultato:
            raise ErroreStrumento(f"Lo stesso {cosa} compare due volte: indicalo una volta sola con il totale delle ore.")
        ore = round(float(riga.get("ore") or 0), 2)
        if not 0 < ore <= 24:
            raise ErroreStrumento(f"Ore non valide ({ore}) per un {cosa}: devono essere tra 0,5 e 24.")
        risultato[risorsa_id] = ore
    return risultato


# ------------------------------------------------------------------ conversazione con Claude

def _carica_conversazione(numero: str) -> Optional[dict]:
    righe = supabase.table("whatsapp_conversazioni").select("*").eq("numero", numero).limit(1).execute().data
    return righe[0] if righe else None


def _messaggi_salvati(conversazione: dict) -> Optional[list]:
    """Cronologia salvata, o None se e' stata scritta con un'altra VERSIONE (non riutilizzabile)."""
    salvati = conversazione.get("messaggi")
    if isinstance(salvati, dict) and salvati.get("versione") == VERSIONE:
        return salvati.get("messaggi") or []
    return None


def _sessione_attiva(conversazione: Optional[dict], user_id: str) -> bool:
    if not conversazione or conversazione.get("user_id") != user_id:
        return False
    messaggi = _messaggi_salvati(conversazione)
    aggiornata = datetime.fromisoformat(conversazione["aggiornato_at"])
    return (
        messaggi is not None
        and datetime.now(FUSO) - aggiornata <= DURATA_SESSIONE
        and len(messaggi) < MAX_MESSAGGI_SESSIONE
    )


def _salva_conversazione(numero: str, user_id: str, messaggi: list, bozza: Optional[dict]) -> None:
    supabase.table("whatsapp_conversazioni").upsert({
        "numero": numero,
        "user_id": user_id,
        "messaggi": {"versione": VERSIONE, "messaggi": messaggi},
        "bozza": bozza,
        "aggiornato_at": datetime.now(FUSO).isoformat(),
    }, on_conflict="numero").execute()


def _chiama_claude(messaggi: list):
    client = get_anthropic_client()
    parametri = dict(
        model=MODELLO,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=STRUMENTI,
        messages=messaggi,
        output_config={"effort": IMPEGNO},
        # Cache automatica: system, strumenti e conversazione fin qui non si ripagano per intero
        # a ogni messaggio.
        cache_control={"type": "ephemeral"},
    )
    if MODELLO in _MODELLI_CON_RIPIEGO:
        return client.beta.messages.create(**parametri, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    return client.messages.create(**parametri)


def _conversa(numero: str, profilo: Profilo, testo: str, vocale: bool) -> tuple[str, dict]:
    """Risposta di Claude al messaggio. La cronologia si salva solo aggiungendo in coda (blocchi di
    ragionamento compresi, invariati): modificarla invaliderebbe il ragionamento gia' fatto."""
    conversazione = _carica_conversazione(numero)
    attiva = _sessione_attiva(conversazione, profilo.id)
    messaggi = list(_messaggi_salvati(conversazione)) if attiva else []
    bozza = conversazione.get("bozza") if attiva else None
    if bozza and _bozza_scaduta(bozza):
        bozza = None

    adesso = datetime.now(FUSO)
    intestazione = f"[{GIORNI[adesso.weekday()]} {adesso:%d/%m/%Y}, ore {adesso:%H:%M}" + (" · messaggio vocale trascritto" if vocale else "") + "]"
    messaggi.append({"role": "user", "content": f"{intestazione}\n{testo}"})

    stato = {"bozza": bozza, "bozza_nuova": False, "salvato": None}
    strumenti = Strumenti(profilo.company_id, stato)
    risposta_testo = ""
    for _ in range(MAX_PASSAGGI):
        risposta = _chiama_claude(messaggi)
        uso = risposta.usage
        logger.info(
            f"WhatsApp Claude {risposta.model}: input {uso.input_tokens} (+cache letti {uso.cache_read_input_tokens or 0}, "
            f"scritti {uso.cache_creation_input_tokens or 0}), output {uso.output_tokens}, stop {risposta.stop_reason}"
        )
        messaggi.append({"role": "assistant", "content": [blocco.to_dict() for blocco in risposta.content]})
        if risposta.stop_reason == "refusal":
            risposta_testo = "Mi dispiace, su questo non posso aiutarti. Posso mostrarti mezzi, operai e rapportini, o registrarne uno."
            break
        if risposta.stop_reason == "pause_turn":
            continue
        chiamate = [b for b in risposta.content if b.type == "tool_use"]
        if not chiamate or risposta.stop_reason != "tool_use":
            risposta_testo = "\n".join(b.text for b in risposta.content if b.type == "text").strip()
            break
        risultati = []
        for chiamata in chiamate:
            esito, errore = strumenti.esegui(chiamata.name, dict(chiamata.input or {}))
            risultati.append({"type": "tool_result", "tool_use_id": chiamata.id, "content": esito, **({"is_error": True} if errore else {})})
        messaggi.append({"role": "user", "content": risultati})
    else:
        risposta_testo = "Non sono riuscito a completare la richiesta: prova a dirmela in modo più semplice."

    # Una chiamata a uno strumento rimasta senza risultato renderebbe invalida la cronologia.
    ultimo = messaggi[-1]
    if ultimo["role"] == "assistant":
        sospese = [b for b in ultimo["content"] if b.get("type") == "tool_use"]
        if sospese:
            messaggi.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b["id"], "content": "Interrotto.", "is_error": True} for b in sospese
            ]})

    _salva_conversazione(numero, profilo.id, messaggi, stato["bozza"])
    return risposta_testo, stato


# ------------------------------------------------------------------ messaggi in arrivo

_blocchi: dict[str, threading.Lock] = {}
_blocchi_lock = threading.Lock()


def _blocco(numero: str) -> threading.Lock:
    """Un messaggio alla volta per numero: due vocali di fila non si sovrappongono."""
    with _blocchi_lock:
        return _blocchi.setdefault(numero, threading.Lock())


def _nuovo_messaggio(wamid: str, numero: str, tipo: str) -> bool:
    """Registra il messaggio; False se era gia' arrivato (Meta a volte lo consegna due volte)."""
    inseriti = (
        supabase.table("whatsapp_messaggi")
        .upsert({"wamid": wamid, "numero": numero, "tipo": tipo}, on_conflict="wamid", ignore_duplicates=True)
        .execute()
        .data
    )
    return bool(inseriti)


def _rispondi_numero_sconosciuto(numero: str) -> None:
    # Al massimo una risposta al giorno a un numero non collegato (ogni risposta ha un costo).
    giorno_fa = (datetime.now(FUSO) - timedelta(days=1)).isoformat()
    recenti = (
        supabase.table("whatsapp_messaggi").select("wamid", count="exact")
        .eq("numero", numero).gte("created_at", giorno_fa).limit(1).execute().count
    )
    if recenti and recenti > 1:
        return
    invia_testo(
        numero,
        "Ciao! Questo numero non è collegato a nessun account APS Light. Per usare l'assistente, "
        "aggiungilo nel gestionale da *Il mio profilo › WhatsApp*.",
    )


def _gestisci_pulsante(numero: str, profilo: Profilo, id_pulsante: str) -> None:
    azione, _, bozza_id = id_pulsante.partition(":")
    conversazione = _carica_conversazione(numero)
    bozza = (conversazione or {}).get("bozza")
    if (
        not bozza
        or bozza.get("id") != bozza_id
        or _bozza_scaduta(bozza)
        or conversazione.get("user_id") != profilo.id
        or azione not in ("conferma", "annulla")
    ):
        invia_testo(numero, "Questo riepilogo non è più valido (è scaduto o è già stato gestito). Se serve, rimandami il rapportino.")
        return

    if azione == "conferma":
        esito = _applica_bozza(profilo.company_id, bozza)
        nota = "Ho premuto «Conferma»: il rapportino è stato salvato."
    else:
        esito = "Ok, non ho salvato nulla."
        nota = "Ho premuto «Annulla»: il rapportino non è stato salvato."
    # La scelta resta nella conversazione, cosi' l'assistente sa com'e' andata (es. "aggiungi anche Rossi").
    messaggi = list(_messaggi_salvati(conversazione)) if _sessione_attiva(conversazione, profilo.id) else []
    if messaggi:
        messaggi.append({"role": "user", "content": nota})
    _salva_conversazione(numero, profilo.id, messaggi, None)
    invia_testo(numero, esito)


def _invia_risposta(numero: str, risposta: str, stato: dict, trascrizione: Optional[str]) -> None:
    prefisso = f"Vocale: _«{trascrizione[:300]}»_\n\n" if trascrizione else ""
    bozza = stato.get("bozza")
    if stato.get("bozza_nuova") and bozza:
        # Solo il riepilogo calcolato dal gestionale (non il testo di Claude, che tende a ripeterlo
        # a parole): e' quello che verra' salvato premendo Conferma.
        invia_pulsanti(numero, prefisso + bozza["riepilogo"], [(f"conferma:{bozza['id']}", "Conferma"), (f"annulla:{bozza['id']}", "Annulla")])
    else:
        invia_testo(numero, prefisso + (risposta or stato.get("salvato") or "Fatto."))


def _elabora(numero: str, wamid: str, tipo: str, messaggio: dict) -> None:
    collegamento = supabase.table("whatsapp_numeri").select("user_id").eq("numero", numero).limit(1).execute().data
    if not collegamento:
        _rispondi_numero_sconosciuto(numero)
        return
    user_id = collegamento[0]["user_id"]
    supabase.table("whatsapp_messaggi").update({"user_id": user_id}).eq("wamid", wamid).execute()

    # Stesse regole del gestionale: un account in attesa o bloccato non usa nemmeno l'assistente.
    profilo = carica_profilo(user_id, fresco=True)
    if not profilo or not profilo.company_id:
        invia_testo(numero, "Il tuo account APS Light non risulta attivo. Contatta l'amministratore.")
        return
    if profilo.stato != "attivo":
        invia_testo(numero, MESSAGGI_STATO.get(profilo.stato, "Il tuo account APS Light non è attivo."))
        return

    segna_letto(wamid)
    vocale = False
    if tipo == "interactive":
        risposta = (messaggio.get("interactive") or {}).get("button_reply") or {}
        _gestisci_pulsante(numero, profilo, risposta.get("id", ""))
        return
    if tipo == "text":
        testo = (messaggio.get("text") or {}).get("body", "")
    elif tipo == "button":
        testo = (messaggio.get("button") or {}).get("text", "")
    elif tipo == "audio":
        vocale = True
        try:
            audio, mime = scarica_media(messaggio["audio"]["id"])
            testo = trascrivi(audio, mime, _suggerimento_trascrizione(profilo.company_id))
        except TrascrizioneNonRiuscita as e:
            invia_testo(numero, str(e))
            return
    else:
        invia_testo(numero, "Per ora capisco solo messaggi scritti e vocali.")
        return

    testo = (testo or "").strip()[:2000]
    if not testo:
        return
    supabase.table("whatsapp_messaggi").update({"testo": testo}).eq("wamid", wamid).execute()
    risposta, stato = _conversa(numero, profilo, testo, vocale)
    _invia_risposta(numero, risposta, stato, testo if vocale else None)


def gestisci_messaggio(messaggio: dict) -> None:
    """Elabora un messaggio ricevuto dal webhook (in background, dopo aver risposto a Meta)."""
    numero, wamid, tipo = messaggio.get("from"), messaggio.get("id"), messaggio.get("type", "")
    if not numero or not wamid:
        return
    try:
        if not _nuovo_messaggio(wamid, numero, tipo):
            return
    except Exception:
        logger.error(f"WhatsApp: impossibile registrare il messaggio {wamid}: {traceback.format_exc()}")
        return
    with _blocco(numero):
        try:
            _elabora(numero, wamid, tipo, messaggio)
        except Exception:
            logger.error(f"WhatsApp: errore elaborando il messaggio {wamid}: {traceback.format_exc()}")
            try:
                invia_testo(numero, "Si è verificato un problema e non ho potuto rispondere. Riprova tra qualche minuto.")
            except Exception:
                pass
