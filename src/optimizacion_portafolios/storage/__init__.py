"""Persistence of tracked portfolios (SQLAlchemy models and repositories).

The database lives in ``DATABASE_URL`` (default ``instance/atlas.db``, outside
version control). Users are InsForge accounts: tables store the InsForge user
id, so there is no local users table. Schema changes are Flask-Migrate
revisions in ``storage/migrations``.
"""

import sqlite3
from pathlib import Path

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

MIGRATIONS_DIRECTORY = str(Path(__file__).with_name("migrations"))


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
migrate = Migrate()


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(connection, _record) -> None:
    """SQLite ignores foreign keys (and ON DELETE CASCADE) unless enabled."""
    if isinstance(connection, sqlite3.Connection):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
