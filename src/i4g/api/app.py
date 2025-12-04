"""FastAPI app factory for i4g Analyst Review API."""

import threading
import time
import uuid
from threading import Lock
from typing import Dict

from fastapi import APIRouter, FastAPI, HTTPException, Request

from i4g.api.account_list import router as account_list_router
from i4g.api.discovery import router as discovery_router
from i4g.api.intake import router as intake_router
from i4g.api.reports import router as reports_router
from i4g.api.review import router as review_router
from i4g.settings import get_settings

# ----------------------------------------
# Task Status API (Step 2 of M6.3)
# ----------------------------------------

task_router = APIRouter(prefix="/tasks", tags=["tasks"])

# Simple in-memory store (replace later with Redis or DB-backed worker registry)
TASK_STATUS: Dict[str, Dict[str, str]] = {}


@task_router.get("/{task_id}")
def get_task_status(task_id: str):
    """
    Retrieve the current status of a background task.
    This endpoint is used by Streamlit or external clients
    to monitor report generation, ingestion, or review actions.
    """
    if task_id not in TASK_STATUS:
        return {"task_id": task_id, "status": "unknown", "message": "Task not found"}

    return {"task_id": task_id, **TASK_STATUS[task_id]}


@task_router.post("/{task_id}/update")
def update_task_status(task_id: str, payload: Dict[str, str]):
    """
    Update or register a task status entry.
    This simulates what a background worker would do.
    Example payload:
        {"status": "in_progress", "message": "Generating report..."}
    """
    TASK_STATUS[task_id] = payload
    return {"task_id": task_id, "updated": True}


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(title="i4g Analyst Review API", version="0.1")
    app.include_router(review_router, prefix="/reviews", tags=["reviews"])
    app.include_router(account_list_router)
    app.include_router(discovery_router)
    app.include_router(intake_router)
    app.include_router(reports_router)
    app.include_router(task_router)

    return app


# For uvicorn, expose `app` at module level
app = create_app()
SETTINGS = get_settings()

# ----------------------------------------
# Simple Rate Limiting and Queue Control
# ----------------------------------------

# In-memory request log (in production, replace with Redis or PostgreSQL table)
REQUEST_LOG = {}
MAX_REQUESTS_PER_MINUTE = 0 if getattr(SETTINGS, "is_local", False) else 10


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """
    Basic per-IP rate limiter. Blocks clients that exceed
    MAX_REQUESTS_PER_MINUTE requests in a rolling 60s window.
    """
    if MAX_REQUESTS_PER_MINUTE <= 0:
        return await call_next(request)
    client_ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")
    now = time.time()
    window_start = now - 60

    # Get or create the list of timestamps for the client IP
    timestamps = REQUEST_LOG.setdefault(client_ip, [])

    # Remove old timestamps in-place
    timestamps[:] = [t for t in timestamps if t > window_start]

    # Check if the rate limit is exceeded
    if len(timestamps) >= MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    # Log the new request timestamp
    timestamps.append(now)

    response = await call_next(request)
    return response


# ----------------------------------------
# Simple Queue Lock for Report Generation
# ----------------------------------------

report_lock = Lock()


@app.post("/reports/generate")
def generate_report_trigger():
    """
    Simulate a guarded entry to report generation.
    Ensures only one concurrent report build at a time.
    """
    if not report_lock.acquire(blocking=False):
        raise HTTPException(status_code=423, detail="Report generation already in progress")

    task_id = str(uuid.uuid4())

    def _run_report():
        try:
            TASK_STATUS[task_id] = {
                "status": "in_progress",
                "message": "Generating report...",
            }
            time.sleep(5)  # Simulate work
            TASK_STATUS[task_id] = {
                "status": "done",
                "message": "Report generated successfully.",
            }
        finally:
            report_lock.release()  # Release lock when thread is done

    thread = threading.Thread(target=_run_report)
    thread.start()

    return {"status": "started", "task_id": task_id}


# Expose REQUEST_LOG for testing purposes
__all__ = ["app", "REQUEST_LOG"]
