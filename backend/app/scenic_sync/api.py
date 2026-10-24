from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from . import service
from .models import PublishRequest, ReviewRequest, SyncSettings, VenueBindingRequest
from .provider import QmsyunProvider

router = APIRouter(prefix="/api/scenic", tags=["Scenic schedules"])
admin_router = APIRouter(prefix="/api/admin/scenic", tags=["Scenic schedule administration"])


@router.get("/parks/{park_id}/performances")
def performances(park_id: str, date: date):
    return service.performances(service.database_url(), park_id, date)


@router.get("/parks/{park_id}/schedule-status")
def schedule_status(park_id: str, date: date):
    return service.status(service.database_url(), park_id, date)


@router.get("/parks/{park_id}/schedule-changes")
def schedule_changes(park_id: str, date: date):
    return service.changes(service.database_url(), park_id, date)


@admin_router.post("/parks/qmsy/sync")
def sync(reviewer: str = Depends(service.require_admin)):
    if os.getenv("SCENIC_QMSY_SYNC_ENABLED") != "1" or os.getenv("SCENIC_SOURCE_POLICY_REVIEWED") != "1":
        raise HTTPException(403, detail="站点政策与授权尚未确认，定时或手动同步未启用。")
    settings = SyncSettings(database_url=service.database_url(),
        minimum_interval_minutes=int(os.getenv("SCENIC_MIN_INTERVAL_MINUTES", "60")))
    result = service.sync_qmsy(settings.database_url, QmsyunProvider(settings))
    result["operator"] = reviewer
    return result


@admin_router.post("/parks/wansui/imports")
async def import_csv(file: UploadFile = File(...), evidence_file: str = Form(...), request_id: str = Form(...),
                     reviewer: str = Depends(service.require_admin)):
    if file.filename != "wansui.csv" and not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(422, detail="只接受CSV节目表。")
    content = await file.read(1_000_001)
    await file.close()
    if len(content) > 1_000_000:
        raise HTTPException(413, detail="CSV过大。")
    # Uploaded evidence is a protected absolute server-side path, never browser file access.
    root = Path(os.getenv("SCENIC_EVIDENCE_ROOT", "")).resolve()
    if not str(root) or root == Path("/"):
        raise HTTPException(503, detail="尚未配置受保护的证据附件目录。")
    return service.import_wansui(service.database_url(), content, evidence_file, root, reviewer, request_id)


@admin_router.post("/parks/{park_id}/schedules/{schedule_id}/review")
def review(park_id: str, schedule_id: int, body: ReviewRequest, reviewer: str = Depends(service.require_admin)):
    return service.review(service.database_url(), park_id, schedule_id, body, reviewer)


@admin_router.put("/parks/{park_id}/venue-bindings")
def bind(park_id: str, body: VenueBindingRequest, reviewer: str = Depends(service.require_admin)):
    return service.bind_venue(service.database_url(), park_id, body, reviewer)


@admin_router.post("/parks/{park_id}/schedules/{schedule_id}/publish")
def publish(park_id: str, schedule_id: int, body: PublishRequest, reviewer: str = Depends(service.require_admin)):
    if not body.confirmed_verified_routes:
        raise HTTPException(422, detail="请确认所有相关场地已接入真实、有效的有向路由数据。")
    return service.publish(service.database_url(), park_id, schedule_id, body, reviewer)

