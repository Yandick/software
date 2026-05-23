from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings, validate_production_settings
from .database import init_db
from .routers.accounts import router as accounts_router
from .routers.audit import router as audit_router
from .routers.auth import router as auth_router
from .routers.demo import router as demo_router
from .routers.issues import router as issues_router
from .routers.knowledge import router as knowledge_router
from .routers.qa import router as qa_router
from .routers.system import router as system_router
from .services.issues_service import ensure_upload_dir


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    validate_production_settings(get_settings())
    init_db()
    ensure_upload_dir()
    yield


app = FastAPI(title="运维数字员工系统", lifespan=lifespan)
app.include_router(accounts_router)
app.include_router(audit_router)
app.include_router(auth_router)
app.include_router(demo_router)
app.include_router(issues_router)
app.include_router(knowledge_router)
app.include_router(qa_router)
app.include_router(system_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
ensure_upload_dir()
