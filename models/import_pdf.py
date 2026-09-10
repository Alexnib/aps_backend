from pydantic import BaseModel
from typing import List, Optional


# --- Computo Metrico ---

class VoceComputoConferma(BaseModel):
    n_voce: Optional[str] = None
    codice_tariffa: Optional[str] = None
    descrizione_lavorazione: Optional[str] = None
    unita_misura: str
    quantita_prevista: float
    importo_unitario: float
    importo_totale: Optional[float] = None


class ConfermaComputoRequest(BaseModel):
    cantiere_id: str
    voci: List[VoceComputoConferma]


class VoceComputoUpdate(BaseModel):
    n_voce: Optional[str] = None
    codice_tariffa: Optional[str] = None
    descrizione_lavorazione: Optional[str] = None
    unita_misura: Optional[str] = None
    quantita_prevista: Optional[float] = None
    importo_unitario: Optional[float] = None
    percentuale_completamento: Optional[float] = None


# --- Preventivo Fornitore ---

class VocePreventivoConferma(BaseModel):
    codice_articolo: Optional[str] = None
    descrizione: str
    unita_misura: str
    prezzo_unitario: float


class ConfermaPreventivoRequest(BaseModel):
    fornitore_id: str
    # None = il prezzo vale per tutte le commesse; valorizzato = prezzo dedicato a quel cantiere.
    cantiere_id: Optional[str] = None
    data_offerta: Optional[str] = None
    voci: List[VocePreventivoConferma]


# --- DDT ---

class RigaDdtConferma(BaseModel):
    codice: Optional[str] = None
    descrizione: str
    unita_misura: str
    quantita: float
    # Prezzo unitario indicato/corretto manualmente in fase di revisione. None = usa il prezzo
    # gia' noto in anagrafica (se esiste); se non esiste ancora nessun prezzo e non viene indicato
    # qui, la voce viene importata con prezzo 0 e andra' corretta a mano in anagrafica.
    prezzo_unitario: Optional[float] = None


class DocumentoDdtConferma(BaseModel):
    cantiere_id: str
    fornitore_id: str
    numero_documento: str
    data_ddt: str
    righe: List[RigaDdtConferma]


class ConfermaDdtRequest(BaseModel):
    documenti: List[DocumentoDdtConferma]
