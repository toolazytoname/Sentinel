"""Shared test wiring for the ai-service suite.

RT.1: the FastAPI ``get_db`` dependency is overridden with a session bound to a
per-test SQLite engine, but ``ReflectionWriter`` persists via the module-level
``get_session()`` (``app.db.models._SessionLocal``). Unless both point at the
SAME engine, the writer persists to a different database than the request/test
reads back — reflections silently vanish (``reflection_id=None``).

``bind_module_engine`` points the module singleton at the given test engine so
``get_session()`` and the ``get_db`` override share one database.
``reset_module_engine`` restores the singleton to ``None`` so tests stay
isolated. Production behavior is untouched: in prod ``get_db`` initializes the
same module engine before calling ``get_session()`` — already consistent.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

import app.db.models as models
from app.deps import reset_caches_for_testing


def bind_module_engine(test_engine) -> sessionmaker[Session]:
    """Point the module-level engine/session factory at ``test_engine``.

    Returns the ``sessionmaker`` so callers can build their ``get_db`` override
    from the exact same factory ``get_session()`` uses, guaranteeing one shared
    database within the test.
    """
    session_local = sessionmaker(bind=test_engine, expire_on_commit=False)
    models._engine = test_engine
    models._SessionLocal = session_local
    return session_local


def reset_module_engine() -> None:
    """Clear the module engine singleton so the next test rebuilds cleanly."""
    models._engine = None
    models._SessionLocal = None
    reset_caches_for_testing()
