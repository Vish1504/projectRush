# /Users/vish1504/projectRush/app/db/database.py

# Database configuration

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


DATABASE_URL = "postgresql+psycopg://vish1504@localhost:5432/rush"

# Engine is SQLAlchemy's gateway to PostgreSQL
engine = create_engine(DATABASE_URL)

# SessionLocal will be a factory that creates Sessions
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)

# To create one Session for the request/unit of work
def get_db():
    # this is an instance of a SQLAlchemy Session 
    db = SessionLocal() 

    try:
        # Now we give that Session to whoever needs it
        yield db
    finally:
        # db has to be closed no matter the result
        db.close()