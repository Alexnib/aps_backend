from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional

# Supabase Auth salva le password con bcrypt, che considera solo i primi 72 byte: oltre quella
# soglia due password diverse con lo stesso inizio risulterebbero uguali.
PASSWORD_MIN_CARATTERI = 8
PASSWORD_MAX_BYTE = 72

# Le password piu' usate in assoluto: rispettano la lunghezza minima ma si indovinano al primo
# tentativo. Stessa lista del controllo lato frontend (src/lib/authValidation.ts).
PASSWORD_TROPPO_COMUNI = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "123123123", "11111111", "00000000", "87654321", "qwerty123", "qwertyuiop", "abcd1234",
    "abc12345", "iloveyou", "aaaaaaaa", "asdfghjkl", "cantiere", "cantiere1", "apslight",
}


def _email_normalizzata(valore):
    return valore.strip().lower() if isinstance(valore, str) else valore


def _testo_obbligatorio(valore: str, campo: str, max_caratteri: int, femminile: bool = False) -> str:
    pulito = " ".join((valore or "").split())
    if not pulito:
        raise ValueError(f"{campo} è obbligatori{'a' if femminile else 'o'}")
    if len(pulito) > max_caratteri:
        raise ValueError(f"{campo} può contenere al massimo {max_caratteri} caratteri")
    return pulito


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    nome: str
    cognome: str
    partita_iva: str
    indirizzo: str

    _normalizza_email = field_validator("email", mode="before")(_email_normalizzata)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v) < PASSWORD_MIN_CARATTERI:
            raise ValueError(f"La password deve avere almeno {PASSWORD_MIN_CARATTERI} caratteri")
        if len(v.encode("utf-8")) > PASSWORD_MAX_BYTE:
            raise ValueError("La password è troppo lunga (massimo 72 caratteri)")
        if not v.strip():
            raise ValueError("La password non può essere composta solo da spazi")
        if v.lower() in PASSWORD_TROPPO_COMUNI:
            raise ValueError("Questa password è tra le più usate in assoluto: scegline un'altra")
        return v

    @field_validator("nome")
    @classmethod
    def _nome(cls, v: str) -> str:
        return _testo_obbligatorio(v, "Il nome", 80)

    @field_validator("cognome")
    @classmethod
    def _cognome(cls, v: str) -> str:
        return _testo_obbligatorio(v, "Il cognome", 80)

    @field_validator("indirizzo")
    @classmethod
    def _indirizzo(cls, v: str) -> str:
        return _testo_obbligatorio(v, "L'indirizzo", 200)

    @field_validator("partita_iva")
    @classmethod
    def _partita_iva(cls, v: str) -> str:
        # Nessun controllo di formato: deve essere possibile registrare anche partite IVA di prova.
        return _testo_obbligatorio(v, "La partita IVA", 30, femminile=True)

class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str

    _normalizza_email = field_validator("email", mode="before")(_email_normalizzata)

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
