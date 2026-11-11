# /Users/vish1504/projectRush/app/schemas/decision.py
from pydantic import BaseModel,Field
from enum import Enum


class DecisionRequest(BaseModel):
    region: str
    device: str
    subscription_tier: str
    viewer_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    break_duration_seconds: int = Field(gt=0)
class DecisionStatusEnum(str, Enum):
    ALLOCATED = "ALLOCATED"
    NO_FILL = "NO_FILL"
    DEGRADED_FALLBACK = "DEGRADED_FALLBACK"


class PodSlotSource(str, Enum):
    COMMERCIAL = "COMMERCIAL"
    HOUSE = "HOUSE"


class PodSlot(BaseModel):
    campaign_id: int | None = None
    duration_seconds: int = Field(gt=0)
    source: PodSlotSource


class DecisionResponse(BaseModel):
    request_id: str = Field(min_length=1)
    status: DecisionStatusEnum
    slots: list[PodSlot] = Field(default_factory=list)
    filled_duration_seconds: int = Field(ge=0)
    unfilled_duration_seconds: int = Field(ge=0)