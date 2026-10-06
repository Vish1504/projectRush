# /Users/vish1504/projectRush/app/schemas/campaign.py

# It defines what Rush is willing to accept and return through the API.
from pydantic import BaseModel, ConfigDict, Field, model_validator
from datetime import datetime
from enum import Enum

class CampaignStatus(str, Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# This is the shape of an incoming request & Pydantic validates it
class CampaignCreate(BaseModel):
    name: str = Field(min_length=1)
    capacity: int = Field(gt=0)
    start_time: datetime
    end_time: datetime
    regions: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    subscription_tiers: list[str] = Field(default_factory=list)
    # How many times per hour can a viewer see this campaign?
    frequency_cap_per_hour: int =Field(gt=0)

    # end_time > start_time
    @model_validator(mode="after")
    def validate_time_window(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")

        return self


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(gt=0)
    name: str
    capacity: int
    start_time: datetime
    end_time: datetime
    status: CampaignStatus
    # How many times per hour can a viewer see this campaign?
    frequency_cap_per_hour: int =Field(gt=0)