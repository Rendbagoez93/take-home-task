from collections.abc import Iterator
from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from app.config import settings

# The API and the Celery worker write to app.db at the same time (ADR-06).
SQLITE_BUSY_TIMEOUT_MS = 5000


def create_db_engine(database_url: str) -> Engine:
    # FastAPI runs sync endpoints in a threadpool, so a connection may cross threads.
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", set_sqlite_pragmas)
    return engine


def set_sqlite_pragmas(dbapi_connection: SQLiteConnection, _record: ConnectionPoolEntry) -> None:
    # WAL lets readers run alongside the single writer; busy_timeout waits instead of
    # failing with "database is locked" (ADR-06).
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


engine = create_db_engine(settings.database_url)
SessionLocal = sessionmaker(engine)


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db
