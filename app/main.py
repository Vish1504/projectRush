# /Users/vish1504/projectRush/app/main.py
from fastapi import FastAPI

from app.api.campaign import router as campaign_router
from app.api.decision import router as decision_router
from contextlib import asynccontextmanager

from app.db.database import SessionLocal
from app.repositories.campaign_repository import CampaignRepository
from app.services.inverted_index import InvertedIndex
from app.runtime.redis_client import redis_client

import os
from uuid import uuid4
from app.runtime.campaign_lease_manager import CampaignLeaseManager


@asynccontextmanager
async def lifespan(app: FastAPI):

    # Create this worker's inverted index
    index = InvertedIndex()

    # Open DB session only for loading the index
    db = SessionLocal()

    try:
        repository = CampaignRepository(db)

        version = redis_client.get("rush:index:version")

        if version is None:
            raise RuntimeError("Redis index version is missing")

        repository.load_index(
            index,
            version=int(version),
        )

    finally:
        db.close()

    # Unique identity for this specific worker process
    worker_id = str(uuid4())

    # Configurable number of campaign units requested per lease refill
    lease_size = int(os.getenv("RUSH_LEASE_SIZE", "100"))

    # Create ONE lease manager for this worker's lifetime
    campaign_lease_manager = CampaignLeaseManager(
        worker_id=worker_id,
        lease_size=lease_size,
    )

    # Make both runtime objects available to requests handled by this worker
    app.state.inverted_index = index
    app.state.campaign_lease_manager = campaign_lease_manager

    yield

app = FastAPI(lifespan=lifespan)
app.include_router(campaign_router)
app.include_router(decision_router)


@app.get("/health")
def getResults():
    return {"status": "ok"}




