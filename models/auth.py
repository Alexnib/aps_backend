from pydantic import BaseModel, EmailStr
from typing import Optional

class CompanyRegister(BaseModel):
    partita_iva: str
    indirizzo: str
    # Assuming 'ragione sociale' might be useful, but schema has partita_iva, indirizzo.
    # The client table has 'name', but we are registering the company.
    # Actually, the user registering is the company. Wait, the DB schema has:
    # companies: id, partita_iva, indirizzo.
    # If the user wants a company name, maybe it's not in the 'companies' table? 
    # Ah! The schema is: "partita_iva", "indirizzo". Let's stick to it.

class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    nome: str
    cognome: str
    partita_iva: str
    indirizzo: str

class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user_id: str
    company_id: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class AziendaInfo(BaseModel):
    partita_iva: Optional[str] = None
    indirizzo: Optional[str] = None


class UserMeResponse(BaseModel):
    id: str
    email: Optional[str] = None
    nome: str
    cognome: str
    ruolo: str
    company_id: str
    azienda: Optional[AziendaInfo] = None


class UserUpdateRequest(BaseModel):
    nome: Optional[str] = None
    cognome: Optional[str] = None
