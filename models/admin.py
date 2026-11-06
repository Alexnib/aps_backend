from typing import Literal

from pydantic import BaseModel


class StatoUtenteUpdate(BaseModel):
    """Abilita (anche un account in attesa) o blocca un utente."""
    stato: Literal["attivo", "bloccato"]
