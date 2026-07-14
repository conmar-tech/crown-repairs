"""Crown Jewelry repair orders admin panel."""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from analytics import build_finance_dashboard
from auth import (
    AuthSettings,
    SessionManager,
    install_auth_middleware,
    verify_google_credential,
)
from models import GoogleCredential, StatusPayload
from orders import (
    FirestoreOrderRepository,
    FirestoreSettings,
    RepairsRepositoryError,
    SampleOrderRepository,
    VALID_STATUSES,
)


APP_DIR = Path(__file__).resolve().parent
settings = AuthSettings.from_env()
sessions = SessionManager(settings)
firestore_settings = FirestoreSettings.from_env()
repository = (
    SampleOrderRepository(firestore_settings)
    if os.getenv("REPAIRS_SAMPLE_DATA", "").strip().lower() in {"1", "true", "yes", "on"}
    else FirestoreOrderRepository(firestore_settings)
)

app = FastAPI(
    title="Crown Repairs",
    description="Internal repair order ledger and finance dashboard",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
install_auth_middleware(app, settings, sessions)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def _require_same_origin_action(request: Request) -> None:
    if request.headers.get("X-Requested-With") != "CrownRepairs":
        raise HTTPException(status_code=403, detail="Invalid request origin")


def _repo_error(exc: RepairsRepositoryError) -> HTTPException:
    return HTTPException(status_code=503, detail=f"Firestore unavailable: {exc}")


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "user_email": getattr(request.state, "user_email", ""),
            "user_name": getattr(request.state, "user_name", ""),
        },
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if settings.auth_disabled:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"google_client_id": settings.google_client_id},
    )


@app.post("/auth/callback")
async def auth_callback(request: Request, body: GoogleCredential):
    _require_same_origin_action(request)
    email, name = verify_google_credential(body.credential, settings)
    token = sessions.create(email, name)
    response = JSONResponse({"success": True})
    response.set_cookie(
        "repairs_session",
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("repairs_session", path="/")
    return response


@app.get("/health")
async def health():
    try:
        return repository.health()
    except RepairsRepositoryError as exc:
        raise _repo_error(exc) from exc


@app.get("/api/user")
async def current_user(request: Request):
    return {
        "email": getattr(request.state, "user_email", ""),
        "name": getattr(request.state, "user_name", ""),
    }


@app.get("/api/orders")
async def list_orders(
    status: str | None = Query(default=None),
    q: str = "",
    name: str = "",
    phone: str = "",
    code: str = "",
    period: str = Query(default="", pattern="^(|today|week|month)$"),
    client_key: str = "",
    client_name: str = "",
    client_phone: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Unknown order status")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")
    try:
        return repository.list_orders(
            status=status,
            query=q,
            name=name,
            phone=phone,
            code=code,
            period=period,
            client_key=client_key,
            client_name=client_name,
            client_phone=client_phone,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
    except RepairsRepositoryError as exc:
        raise _repo_error(exc) from exc


@app.patch("/api/orders/{order_id}/status")
async def update_order_status(request: Request, order_id: str, payload: StatusPayload):
    _require_same_origin_action(request)
    try:
        updated = repository.update_status(
            order_id=order_id,
            status=payload.status,
            user_email=str(getattr(request.state, "user_email", "")),
            settle_balance=payload.settleBalance,
        )
    except RepairsRepositoryError as exc:
        raise _repo_error(exc) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return updated


@app.delete("/api/orders/{order_id}")
async def delete_order(request: Request, order_id: str):
    _require_same_origin_action(request)
    try:
        deleted = repository.delete_order(order_id)
    except RepairsRepositoryError as exc:
        raise _repo_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"success": True, "orderId": order_id}


@app.get("/api/clients")
async def list_clients(
    q: str = "",
    sort: str = Query(default="recent", pattern="^(recent|name|orders)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    try:
        return repository.list_clients(query=q, sort=sort, limit=limit, offset=offset)
    except RepairsRepositoryError as exc:
        raise _repo_error(exc) from exc


@app.get("/api/finance")
async def finance_dashboard(
    period: str = Query(default="month", pattern="^(week|month|year|all)$"),
    date_from: date | None = None,
    date_to: date | None = None,
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")
    try:
        orders = repository.all_orders()
    except RepairsRepositoryError as exc:
        raise _repo_error(exc) from exc

    today = datetime.now(firestore_settings.timezone).date()
    return build_finance_dashboard(
        orders,
        today=today,
        period=period,
        date_from=date_from,
        date_to=date_to,
    )
