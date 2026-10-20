from typing import List, Optional

from pydantic import BaseModel, Field


class VoceAffidata(BaseModel):
    voce_id: str
    quantita: float = Field(gt=0)
    importo_unitario: float = Field(ge=0)


class AffidamentoCreate(BaseModel):
    """Affida una o piu' voci del computo metrico di un cantiere alla stessa ditta subappaltatrice."""
    cantiere_id: str
    subappaltatore_id: str
    voci: List[VoceAffidata] = Field(min_length=1)


class AffidamentoUpdate(BaseModel):
    subappaltatore_id: Optional[str] = None
    quantita: Optional[float] = Field(default=None, gt=0)
    importo_unitario: Optional[float] = Field(default=None, ge=0)
