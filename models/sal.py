from pydantic import BaseModel
from typing import List


class PercentualeVoce(BaseModel):
    voce_id: str
    percentuale_completamento: float


class SalCreate(BaseModel):
    cantiere_id: str
    data_sal: str
    percentuali: List[PercentualeVoce]
