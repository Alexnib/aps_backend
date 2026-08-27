from pydantic import BaseModel, Field
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

class CantiereCreate(CantiereBase):
    pass

class CantiereUpdate(BaseModel):
    cliente_id: Optional[str] = None
    codice_interno: Optional[str] = None
    nome_cantiere: Optional[str] = None
    indirizzo_cantiere: Optional[str] = None
    stato: Optional[str] = None
    budget_previsto: Optional[float] = None

class CantiereResponse(CantiereBase):
    id: str
    company_id: str

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
