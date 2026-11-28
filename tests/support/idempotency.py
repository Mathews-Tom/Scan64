"""Route ``IdempotencyMiddleware`` through a test's session for one test."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from sqlmodel import Session

from scan64.api.middleware import IdempotencyMiddleware


@contextmanager
def idempotency_session(app: FastAPI, session: Session) -> Iterator[list[Session]]:
    """Give the middleware ``session`` and yield the sessions it took.

    Starlette builds the middleware stack from ``user_middleware`` on the
    first request and keeps it, so a patch made after an earlier test's
    request would never reach the middleware. Drop the built stack on entry
    so this test's request rebuilds it with the patch, and again on exit so
    the next test rebuilds it with the original arguments.
    """
    handed_out: list[Session] = []

    def get_test_session() -> Generator[Session, None, None]:
        handed_out.append(session)
        yield session

    entries = [
        m
        for m in app.user_middleware
        if isinstance(m.cls, type) and issubclass(m.cls, IdempotencyMiddleware)
    ]
    assert len(entries) == 1, f"expected one IdempotencyMiddleware, found {len(entries)}"
    [entry] = entries
    original_kwargs = dict(entry.kwargs)
    entry.kwargs["get_session_callable"] = get_test_session
    app.middleware_stack = None
    try:
        yield handed_out
    finally:
        entry.kwargs.clear()
        entry.kwargs.update(original_kwargs)
        app.middleware_stack = None
