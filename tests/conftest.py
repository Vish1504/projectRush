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
Base.metadata.drop_all(bind=test_engine)
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



