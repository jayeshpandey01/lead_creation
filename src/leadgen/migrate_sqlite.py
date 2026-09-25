"""Copy local SQLite leads to Firestore (idempotent by Firestore document ID).

Run with STORAGE_BACKEND=firestore and Firebase credentials configured:
    SOURCE_DATABASE_URL=sqlite:///./leadgen.db python -m leadgen.migrate_sqlite
"""
import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .db import get_session, init_db
from .models import Lead
from .settings import settings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if settings.storage_backend != "firestore":
        raise SystemExit("Set STORAGE_BACKEND=firestore before running this migration")

    source_url = os.environ.get("SOURCE_DATABASE_URL", "sqlite:///./leadgen.db")
    connect_args = {"check_same_thread": False} if source_url.startswith("sqlite") else {}
    source_engine = create_engine(source_url, connect_args=connect_args)
    SourceSession = sessionmaker(bind=source_engine)
    init_db()

    source = SourceSession()
    target = get_session()
    migrated = 0
    try:
        for lead in source.query(Lead).order_by(Lead.id.asc()).yield_per(100):
            target.add(lead)
            target.commit()
            migrated += 1
    finally:
        source.close()
        target.close()
        source_engine.dispose()

    logging.info("Copied %d leads from %s to Firestore collection %s", migrated, source_url, settings.firestore_collection)
    return migrated


if __name__ == "__main__":
    main()
