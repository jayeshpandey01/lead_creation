import hashlib
import json
import logging
import operator
import os
from enum import Enum
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList
from sqlalchemy.sql.operators import desc_op
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .settings import settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None
_firestore_client = None
logger = logging.getLogger(__name__)


def _use_firestore() -> bool:
    return settings.storage_backend == "firestore"


def _get_firestore_client():
    global _firestore_client
    if _firestore_client is not None:
        return _firestore_client

    import firebase_admin
    from firebase_admin import credentials, firestore

    try:
        app = firebase_admin.get_app()
    except ValueError:
        options = {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
        if settings.firebase_service_account_json:
            info = json.loads(settings.firebase_service_account_json)
            cred = credentials.Certificate(info)
            if not options and info.get("project_id"):
                options = {"projectId": info["project_id"]}
            app = firebase_admin.initialize_app(cred, options=options)
        elif settings.firebase_service_account_path:
            cred = credentials.Certificate(os.path.expanduser(settings.firebase_service_account_path))
            app = firebase_admin.initialize_app(cred, options=options)
        else:
            if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                raise RuntimeError(
                    "Firestore credentials are missing. Set FIREBASE_SERVICE_ACCOUNT_PATH locally "
                    "or provide FIREBASE_SERVICE_ACCOUNT_JSON to the GitHub Actions workflow."
                )
            # Allows Application Default Credentials in Google-hosted environments.
            app = firebase_admin.initialize_app(options=options)
    _firestore_client = firestore.client(app=app)
    return _firestore_client


def _value(obj):
    if isinstance(obj, Enum):
        return obj.value
    return obj


def _condition(expr, record: dict) -> bool:
    """Evaluate the small SQLAlchemy filter subset used by this app.

    Firestore reads remain isolated behind the current session/query interface,
    allowing the business pipeline to use either local SQLite or Firestore.
    The lead table is intentionally small; filtering in process keeps this
    first migration simple and its query semantics compatible.
    """
    if isinstance(expr, BooleanClauseList):
        tests = [_condition(child, record) for child in expr.clauses]
        return all(tests) if expr.operator is operator.and_ else any(tests)
    if not isinstance(expr, BinaryExpression):
        raise NotImplementedError(f"Unsupported Firestore filter expression: {expr!r}")

    column = expr.left.key
    expected = _value(getattr(expr.right, "value", None))
    actual = record.get(column)
    op = expr.operator
    if op is operator.eq:
        return actual == expected
    if op is operator.ne:
        return actual != expected
    if op is operator.ge:
        return actual is not None and expected is not None and actual >= expected
    if op is operator.gt:
        return actual is not None and expected is not None and actual > expected
    if op is operator.le:
        return actual is not None and expected is not None and actual <= expected
    if op is operator.lt:
        return actual is not None and expected is not None and actual < expected
    if getattr(op, "__name__", "") == "is_not":
        return actual is not expected
    if getattr(op, "__name__", "") == "is_":
        return actual is expected
    raise NotImplementedError(f"Unsupported Firestore comparison: {op!r}")


class _FirestoreQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model
        self.filters = []
        self.max_rows = None
        self.sorts = []

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def order_by(self, *expressions):
        for expression in expressions:
            descending = getattr(expression, "modifier", None) is desc_op
            element = getattr(expression, "element", expression)
            self.sorts.append((element.key, descending))
        return self

    def _all_unlimited(self):
        rows = []
        collection = _get_firestore_client().collection(settings.firestore_collection)
        for snapshot in collection.stream():
            data = snapshot.to_dict() or {}
            data["id"] = data.get("id", snapshot.id)
            if all(_condition(expr, data) for expr in self.filters):
                values = {key: data.get(key) for key in self.model.__table__.columns.keys()}
                enum_class = getattr(self.model.__table__.columns.get("status").type, "enum_class", None)
                if enum_class and values.get("status") is not None:
                    values["status"] = enum_class(values["status"])
                rows.append(self.model(**values))
        for key, descending in reversed(self.sorts):
            rows.sort(key=lambda item: (getattr(item, key) is None, getattr(item, key)), reverse=descending)
        return rows

    def all(self):
        rows = self._all_unlimited()
        return rows[:self.max_rows] if self.max_rows is not None else rows

    def first(self):
        rows = self.limit(1).all()
        return rows[0] if rows else None

    def count(self):
        return len(self._all_unlimited())


class _FirestoreSession:
    def __init__(self):
        self.pending = {}
        self.closed = False

    def query(self, model):
        return _FirestoreQuery(self, model)

    def add(self, lead):
        if not lead.email:
            raise ValueError("Firestore lead requires an email address")
        # A deterministic key makes retries and SQLite imports upsert the same
        # prospect instead of creating a second document with a new auto-ID.
        lead.id = hashlib.sha256(lead.email.strip().lower().encode()).hexdigest()[:24]
        now = datetime.now(timezone.utc)
        if not lead.discovered_at:
            lead.discovered_at = now
        lead.updated_at = now
        self.pending[str(lead.id)] = lead

    def commit(self):
        if not self.pending:
            return
        client = _get_firestore_client()
        batch = client.batch()
        for doc_id, lead in self.pending.items():
            payload = {}
            for column in lead.__table__.columns.keys():
                value = getattr(lead, column)
                if hasattr(value, "value") and hasattr(value, "name"):
                    value = value.value
                if isinstance(value, datetime) and value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                payload[column] = value
            batch.set(client.collection(settings.firestore_collection).document(doc_id), payload, merge=True)
        batch.commit()
        self.pending.clear()

    def close(self):
        self.closed = True


def _get_firestore_session():
    return _FirestoreSession()


def next_discovery_query_index(query_count: int) -> int:
    """Return and persist the next search index across ephemeral job runners."""
    if query_count <= 0:
        return 0
    if not _use_firestore():
        # The local long-lived process maintains its own in-memory cursor.
        return 0
    from google.cloud import firestore as google_firestore

    client = _get_firestore_client()
    ref = client.collection(settings.firestore_metadata_collection).document("discovery")
    transaction = client.transaction()

    @google_firestore.transactional
    def advance(txn):
        snapshot = ref.get(transaction=txn)
        current = int((snapshot.to_dict() or {}).get("query_index", 0)) % query_count
        txn.set(ref, {"query_index": (current + 1) % query_count, "query_count": query_count}, merge=True)
        return current

    return advance(transaction)


def get_engine():
    global _engine
    if _engine is None:
        url = settings.database_url
        connect_args = {}
        if url.startswith("sqlite"):
            # allow the connection to be used from the different threads
            # asyncio.to_thread() runs blocking DB calls on
            connect_args = {"check_same_thread": False}
        _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    return _engine


def get_session():
    if _use_firestore():
        return _get_firestore_session()
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_db():
    from . import models  # noqa: F401  (registers models on Base before create_all)

    if _use_firestore():
        _get_firestore_client()
        logger.info("Firestore storage is ready (collection=%s)", settings.firestore_collection)
        return
    if settings.storage_backend != "sqlite":
        raise ValueError("STORAGE_BACKEND must be either 'sqlite' or 'firestore'")
    Base.metadata.create_all(get_engine())
