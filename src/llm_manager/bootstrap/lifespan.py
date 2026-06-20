"""FastAPI lifespan: start managed services on startup; stop + close store on shutdown."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


def make_lifespan(container):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container.services.start()
        try:
            yield
        finally:
            container.shutdown()

    return lifespan
