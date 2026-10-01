from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.db.database import get_db
from app.db.base import Base
from app.models.campaign import Campaign
import pytest
from sqlalchemy import delete

TEST_DB_URL = "postgresql+psycopg:///test_rush_db"

# Engine is SQLAlchemy's gateway to PostgreSQL (test database)
test_engine = create_engine(TEST_DB_URL)

Base.metadata.create_all(bind=test_engine)
# factory that creates Sessions connected to test_rush_db
TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    expire_on_commit=False,
)

# To create one Session for the request/unit of work
def get_test_db():
    # this is an instance of a SQLAlchemy Session 
    test_db = TestingSessionLocal() 

    try:
        # Now we give that Session to whoever needs it
        yield test_db
    finally:
        # test_db has to be closed no matter the result
        test_db.close()

# Overrides to get_test_db instead of get_db
app.dependency_overrides[get_db] = get_test_db

@pytest.fixture(autouse=True) # run this automatically for every test.
def clean_campaigns():
    db = TestingSessionLocal()

    # Remove every Campaign row before the test starts
    db.execute(delete(Campaign)) # DELETE FROM campaigns;
    db.commit()
    db.close()

    yield
    
@pytest.fixture
def db_session():
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()