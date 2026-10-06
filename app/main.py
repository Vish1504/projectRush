# /Users/vish1504/projectRush/app/main.py
from fastapi import FastAPI

from app.api.campaign import router as campaign_router
from app.api.decision import router as decision_router


app = FastAPI()

app.include_router(campaign_router)
app.include_router(decision_router)


@app.get("/health")
def getResults():
    return {"status": "ok"}




