from pydantic import BaseModel, Field


class NumeroWhatsappCreate(BaseModel):
    """Numero di cellulare autorizzato a usare l'assistente WhatsApp per questo account."""
    nome: str = Field(min_length=1, max_length=60)
    numero: str = Field(min_length=6, max_length=25)
