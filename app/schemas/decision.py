# /Users/vish1504/projectRush/app/schemas/decision.py
from pydantic import BaseModel,Field
from enum import Enum


class DecisionRequest(BaseModel):
    region: str
    device: str
    subscription_tier: str
    viewer_id: str = Field(min_length=1) #Who is asking for an ad?
    request_id: str = Field(min_length=1) # Is this is retry of a processed request?
    
class DecisionStatusEnum(str, Enum):
    ALLOCATED = "ALLOCATED"
    NO_FILL = "NO_FILL"
class DecisionResponse(BaseModel):
    request_id: str = Field(min_length=1) 
    status: DecisionStatusEnum
    campaign_id: int | None
    
    