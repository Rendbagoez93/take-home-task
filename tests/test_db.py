from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.db import get_db


def test_engine_uses_wal_journal_mode(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_engine_sets_busy_timeout(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_get_db_yields_a_session() -> None:
    sessions = get_db()

    assert isinstance(next(sessions), Session)
    sessions.close()
