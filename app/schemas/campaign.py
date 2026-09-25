# /Users/vish1504/projectRush/app/schema/campaign.py
from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from enum import Enum

class CampaignStatus(str, Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1)
    capacity: int = Field(gt=0)
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_time_window(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")

        return self


class CampaignResponse(BaseModel):
    id: int = Field(gt=0)
    name: str
    capacity: int
    start_time: datetime
    end_time: datetime
    status: CampaignStatus