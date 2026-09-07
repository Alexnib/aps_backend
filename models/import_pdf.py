from pydantic import BaseModel
from typing import List, Optional


# --- Computo Metrico ---

class VoceComputoConferma(BaseModel):
    n_voce: Optional[str] = None
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
    descrizione_lavorazione: Optional[str] = None
    unita_misura: Optional[str] = None
    quantita_prevista: Optional[float] = None
    importo_unitario: Optional[float] = None


# --- DDT ---

class RigaDdtConferma(BaseModel):
    codice: Optional[str] = None
    descrizione: str
    unita_misura: str
    quantita: float


class DocumentoDdtConferma(BaseModel):
    cantiere_id: str
    fornitore_id: str
    numero_documento: str
    data_ddt: str
    righe: List[RigaDdtConferma]


class ConfermaDdtRequest(BaseModel):
    documenti: List[DocumentoDdtConferma]
