from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date

# --- Clienti ---
class ClientBase(BaseModel):
    name: str
    ragione_sociale: str
    partita_iva: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    ragione_sociale: Optional[str] = None
    partita_iva: Optional[str] = None

class ClientResponse(ClientBase):
    id: str
    company_id: str

# --- Cantieri ---
class CantiereBase(BaseModel):
    cliente_id: str
    codice_interno: str
    nome_cantiere: str
    indirizzo_cantiere: Optional[str] = None
    stato: Optional[str] = "Preventivo"
    budget_previsto: Optional[float] = 0.0
    percentuale_avanzamento: Optional[float] = 100.0

class CantiereCreate(CantiereBase):
    pass

class CantiereUpdate(BaseModel):
    cliente_id: Optional[str] = None
    codice_interno: Optional[str] = None
    nome_cantiere: Optional[str] = None
    indirizzo_cantiere: Optional[str] = None
    stato: Optional[str] = None
    budget_previsto: Optional[float] = None
    percentuale_avanzamento: Optional[float] = None

class CantiereResponse(CantiereBase):
    id: str
    company_id: str
    # Derivata dalle voci del computo metrico (media pesata sull'importo), se presenti.
    # None = nessuna voce importata: si usa percentuale_avanzamento come fallback manuale.
    percentuale_effettiva: Optional[float] = None

# --- Operai ---
class OperaioCreate(BaseModel):
    matricola: Optional[str] = None
    nome: str
    cognome: str
    costo_orario: float  # Will be mapped to 'risorse.importo_unitario_standard'

class OperaioUpdate(BaseModel):
    matricola: Optional[str] = None
    nome: Optional[str] = None
    cognome: Optional[str] = None
    costo_orario: Optional[float] = None

class OperaioResponse(BaseModel):
    id: str
    risorsa_id: str
    matricola: Optional[str] = None
    nome: str
    cognome: str
    costo_orario: float

# --- Mezzi ---
class MezzoCreate(BaseModel):
    targa_matricola: str
    descrizione: Optional[str] = None
    costo_orario: float

class MezzoUpdate(BaseModel):
    targa_matricola: Optional[str] = None
    descrizione: Optional[str] = None
    costo_orario: Optional[float] = None

class MezzoResponse(BaseModel):
    id: str
    risorsa_id: str
    targa_matricola: str
    descrizione: Optional[str] = None
    costo_orario: float

# --- Fornitori ---
class FornitoreBase(BaseModel):
    ragione_sociale: str
    partita_iva: Optional[str] = None
    citta: Optional[str] = None
    email: Optional[str] = None

class FornitoreCreate(FornitoreBase):
    pass

class FornitoreUpdate(BaseModel):
    ragione_sociale: Optional[str] = None
    partita_iva: Optional[str] = None
    citta: Optional[str] = None
    email: Optional[str] = None

class FornitoreResponse(FornitoreBase):
    id: str
    azienda_id: str

# --- Subappaltatori ---
# Anagrafica separata dai fornitori: qui ci sono le ditte che eseguono lavorazioni del computo in
# subappalto (SAL Sub), non quelle che vendono materiali.
def _testo_opzionale(valore):
    if isinstance(valore, str):
        valore = valore.strip()
        return valore or None
    return valore


class SubappaltatoreBase(BaseModel):
    ragione_sociale: str
    partita_iva: Optional[str] = None
    citta: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None

    _pulisci_opzionali = field_validator("partita_iva", "citta", "email", "telefono", mode="before")(_testo_opzionale)

    @field_validator("ragione_sociale")
    @classmethod
    def _ragione_sociale(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("La ragione sociale è obbligatoria")
        return v.strip()

class SubappaltatoreCreate(SubappaltatoreBase):
    pass

class SubappaltatoreUpdate(BaseModel):
    ragione_sociale: Optional[str] = None
    partita_iva: Optional[str] = None
    citta: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None

    _pulisci_opzionali = field_validator("partita_iva", "citta", "email", "telefono", mode="before")(_testo_opzionale)

    @field_validator("ragione_sociale")
    @classmethod
    def _ragione_sociale(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("La ragione sociale è obbligatoria")
        return v.strip() if v else v

class SubappaltatoreResponse(SubappaltatoreBase):
    id: str
    azienda_id: str
