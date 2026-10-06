# /Users/vish1504/projectRush/app/schemas/decision.py
from pydantic import BaseModel,Field


class DecisionRequest(BaseModel):
    region: str
    device: str
    subscription_tier: str
    viewer_id: str = Field(min_length=1) #Who is asking for an ad?
    request_id: str = Field(min_length=1) # Is this is retry of a processed request?
    
    