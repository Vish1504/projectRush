# /Users/vish1504/projectRush/app/main.py
from fastapi import FastAPI

from app.api.campaign import router as campaign_router
from app.api.decision import router as decision_router
from contextlib import asynccontextmanager

from app.db.database import SessionLocal
from app.repositories.campaign_repository import CampaignRepository
from app.services.inverted_index import InvertedIndex
from app.runtime.redis_client import redis_client



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create an inverted index
    index=InvertedIndex()
    # Open a database session
    db=SessionLocal()
    
    try:
        repository = CampaignRepository(db)
        # Read the version shared by all Rush workers
        version = redis_client.get("rush:index:version")
        
        if version is None:
            raise RuntimeError("Redis index version is missing")

        # Load PostgreSQL campaign data using this version
        repository.load_index(index, version=int(version))
    
    finally:
        # To close the database session even if loading fails
        db.close()

    # Rush's API endpoints will need to access the loaded index
    app.state.inverted_index = index
    
    yield

app = FastAPI(lifespan=lifespan)
app.include_router(campaign_router)
app.include_router(decision_router)


@app.get("/health")
def getResults():
    return {"status": "ok"}




