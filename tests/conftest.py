import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft.db.base import Base


@pytest.fixture
def db_session():
    """Sessione SQLAlchemy su DB SQLite in-memory, isolata per test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    with TestSession() as session:
        yield session
