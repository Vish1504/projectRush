from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.database import get_db
from app.main import app
from app.models.campaign import Campaign, CampaignLease
from app.runtime.campaign_lease_manager import (
    CampaignLeaseManager,
    LeaseReservationResult,
)
from app.runtime.redis_client import redis_client
from app.services.inverted_index import InvertedIndex


TEST_DB_URL = "postgresql+psycopg:///test_rush_db"

# Engine is SQLAlchemy's gateway to PostgreSQL (test database)
test_engine = create_engine(TEST_DB_URL)

# The test database is recreated manually whenever the schema changes.
Base.metadata.create_all(bind=test_engine)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_test_db():
    test_db = TestingSessionLocal()
    try:
        yield test_db
    finally:
        test_db.close()


# API requests should use test_rush_db instead of the real Rush database.
app.dependency_overrides[get_db] = get_test_db


@pytest.fixture(autouse=True)
def clean_test_state():
    # Clear durable rows created by the previous test.
    db = TestingSessionLocal()
    try:
        db.execute(delete(CampaignLease))
        db.execute(delete(Campaign))
        db.commit()
    finally:
        db.close()

    # Keep every test independent from whatever index version another test used.
    previous_index_version = redis_client.get("rush:index:version")
    redis_client.set("rush:index:version", 0)

    # Tests do not enter FastAPI's production lifespan because that lifespan
    # opens the real SessionLocal. Instead, provide the two worker-scoped
    # runtime objects that /decisions expects.
    app.state.inverted_index = InvertedIndex()

    lease_manager = MagicMock(spec=CampaignLeaseManager)
    lease_manager.reserve_one_unit.return_value = LeaseReservationResult.RESERVED
    app.state.campaign_lease_manager = lease_manager

    yield

    if previous_index_version is None:
        redis_client.delete("rush:index:version")
    else:
        redis_client.set("rush:index:version", previous_index_version)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()




# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker
# from app.main import app
# from app.db.database import get_db
# from app.db.base import Base
# from app.models.campaign import Campaign
# import pytest
# from sqlalchemy import delete

# TEST_DB_URL = "postgresql+psycopg:///test_rush_db"

# # Engine is SQLAlchemy's gateway to PostgreSQL (test database)
# test_engine = create_engine(TEST_DB_URL)

# Base.metadata.create_all(bind=test_engine)
# # factory that creates Sessions connected to test_rush_db
# TestingSessionLocal = sessionmaker(
#     bind=test_engine,
#     autoflush=False,
#     expire_on_commit=False,
# )

# # To create one Session for the request/unit of work
# def get_test_db():
#     # this is an instance of a SQLAlchemy Session 
#     test_db = TestingSessionLocal() 

#     try:
#         # Now we give that Session to whoever needs it
#         yield test_db
#     finally:
#         # test_db has to be closed no matter the result
#         test_db.close()

# # Overrides to get_test_db instead of get_db
# app.dependency_overrides[get_db] = get_test_db

# @pytest.fixture(autouse=True) # run this automatically for every test.
# def clean_campaigns():
#     db = TestingSessionLocal()

#     # Remove every Campaign row before the test starts
#     db.execute(delete(Campaign)) # DELETE FROM campaigns;
#     db.commit()
#     db.close()

#     yield
    
# @pytest.fixture
# def db_session():
#     db = TestingSessionLocal()

#     try:
#         yield db
#     finally:
#         db.close()