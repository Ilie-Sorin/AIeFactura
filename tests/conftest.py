"""Fixture-uri comune: bază de date de test separată (aiefactura_test), pe
același container Postgres de dezvoltare, creată direct din modele (fără
Alembic — viteza contează aici, nu istoricul de migrare) și cu rollback pe
fiecare test pentru izolare."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — populează Base.metadata
from app.config import get_settings
from app.db import Base

TEST_DB_NAME = "aiefactura_test"


@pytest.fixture(scope="session")
def engine():
    owner_url = make_url(get_settings().database_url_migrations)
    admin_url = owner_url.set(database="postgres")

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin_engine.dispose()

    test_engine = create_engine(owner_url.set(database=TEST_DB_NAME))
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(bind=connection)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def db_session_commit(engine):
    """Ca `db_session`, dar sesiunea e legată direct de engine (nu de o
    tranzacție externă), astfel încât testul poate apela `session.commit()`
    la mijloc pentru a simula limite reale de request — izolarea se face
    prin TRUNCATE la final, nu prin rollback."""
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        table_names = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
