from pydantic import BaseModel
from typing import Optional
from datetime import date

# --- Rapportini ---
class RapportinoOperaioCreate(BaseModel):
    cantiere_id: str
    operaio_id: str
    data_lavoro: date
    quantita_ore: float
    descrizione_lavorazione: Optional[str] = None

class RapportinoMezzoCreate(BaseModel):
    cantiere_id: str
    mezzo_id: str
    data_utilizzo: date
    quantita_ore: float

class RapportinoOperaioUpdate(BaseModel):
    cantiere_id: Optional[str] = None
    operaio_id: Optional[str] = None
    data_lavoro: Optional[date] = None
    quantita_ore: Optional[float] = None
    descrizione_lavorazione: Optional[str] = None

class RapportinoMezzoUpdate(BaseModel):
    cantiere_id: Optional[str] = None
    mezzo_id: Optional[str] = None
    data_utilizzo: Optional[date] = None
    quantita_ore: Optional[float] = None

class RapportinoResponse(BaseModel):
    id: str
    cantiere_id: str
    data: date
    quantita: float
    importo_totale: float
