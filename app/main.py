# /Users/vish1504/projectRush/app/main.py
from fastapi import FastAPI
from pydantic import BaseModel,Field
from app.api.campaign import router as campaign_router


app=FastAPI()
app.include_router(campaign_router)


@app.get("/health")
def getResults():
    return {"status": "ok"}




