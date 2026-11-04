import hashlib
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, computed_field


class JobPostingCreate(BaseModel):
    source_code: str = Field(..., description="Pl. 'melodiak', 'mads'")
    external_id: str = Field(..., description="A munka egyedi azonosítója a weboldalon")
    title: str
    category: Optional[str] = None
    city: Optional[str] = None
    is_remote: bool = False
    hourly_wage_min: Optional[int] = None
    hourly_wage_max: Optional[int] = None
    wage_raw_text: Optional[str] = None
    description: Optional[str] = None
    url: str
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def fingerprint(self) -> str:
        """Automatikus SHA-256 hash a deduplikációhoz."""
        raw_key = f"{self.source_code}:{self.external_id}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()