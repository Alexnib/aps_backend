from pydantic import BaseModel
from typing import Optional


class ArticoloAnagraficaCreate(BaseModel):
    nome_materiale: str
    fornitore_id: str
    unita_misura: str
    importo_unitario: float = 0
    codice: Optional[str] = None


class ArticoloAnagraficaUpdate(BaseModel):
    descrizione_articolo: Optional[str] = None
    unita_misura: Optional[str] = None
    importo_unitario: Optional[float] = None
    codice_articolo_fornitore: Optional[str] = None
    fornitore_id: Optional[str] = None
    # None esplicito ("cantiere_id": null nel body) = valido per tutte le commesse.
    cantiere_id: Optional[str] = None


class ConsegnaCreate(BaseModel):
    cantiere_id: str
    articolo_fornitore_id: str
    quantita: float
    data_consegna: str
    numero_documento: Optional[str] = "Manuale"


class ConsegnaUpdate(BaseModel):
    numero_documento: Optional[str] = None
    data_consegna: Optional[str] = None
    quantita: Optional[float] = None
    importo_totale: Optional[float] = None
    articolo_fornitore_id: Optional[str] = None
