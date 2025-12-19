import os
import json
import uuid
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

BUILDER_URL = os.getenv("BUILDER_URL", "http://builder:8090")
WIDGETS_DIR = os.getenv("WIDGETS_DIR", "/data/widgets")

router = APIRouter()


class CreateWidgetReq(BaseModel):
    prompt: str
    instrument: str | None = None


class ChatWidgetReq(BaseModel):
    message: str  # 用户的修改指令


@router.post("/widgets")
async def create_widget(req: CreateWidgetReq):
    wid = str(uuid.uuid4())[:8]
    out_dir = os.path.join(WIDGETS_DIR, wid)
    os.makedirs(out_dir, exist_ok=True)

    meta = {
        "id": wid,
        "prompt": req.prompt,
        "instrument": req.instrument or "",
        "status": "queued",
    }
    meta_path = os.path.join(out_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    async with httpx.AsyncClient(timeout=300.0) as client:
        await client.post(f"{BUILDER_URL}/build", json={
            "id": wid,
            "prompt": req.prompt,
            "instrument": meta["instrument"]
        })

    return {
        "id": wid,
        "widget_url": f"/widgets/{wid}/dist/index.html?instrument={meta['instrument']}",
    }


@router.get("/widgets")
async def list_widgets():
    """List all widgets with their status."""
    widgets = []
    if not os.path.exists(WIDGETS_DIR):
        return {"widgets": []}

    for wid in os.listdir(WIDGETS_DIR):
        meta_path = os.path.join(WIDGETS_DIR, wid, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    # Add creation time from file mtime
                    meta["created_at"] = os.path.getmtime(meta_path)
                    widgets.append(meta)
            except:
                pass

    # Sort by creation time, newest first
    widgets.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return {"widgets": widgets}


@router.get("/widgets/{widget_id}/status")
async def get_widget_status(widget_id: str):
    meta_path = os.path.join(WIDGETS_DIR, widget_id, "meta.json")
    if not os.path.exists(meta_path):
        return {"error": "not found"}
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/widgets/{widget_id}/logs")
async def get_widget_logs(widget_id: str, offset: int = 0):
    """Get build logs for a widget, supports incremental fetching via offset."""
    log_path = os.path.join(WIDGETS_DIR, widget_id, "build.log")
    if not os.path.exists(log_path):
        return {"logs": "", "offset": 0, "done": False}

    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Return content from offset position
    new_content = content[offset:] if offset < len(content) else ""
    new_offset = len(content)

    # Check if build is done (status is ready or failed)
    meta_path = os.path.join(WIDGETS_DIR, widget_id, "meta.json")
    done = False
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
            done = meta.get("status") in ("ready", "failed")

    return {"logs": new_content, "offset": new_offset, "done": done}


@router.post("/widgets/{widget_id}/chat")
async def chat_widget(widget_id: str, req: ChatWidgetReq):
    """Continue conversation with an existing widget to modify it."""
    out_dir = os.path.join(WIDGETS_DIR, widget_id)
    meta_path = os.path.join(out_dir, "meta.json")

    if not os.path.exists(meta_path):
        return {"error": "Widget not found"}

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # Update status to building
    meta["status"] = "building"
    meta["chat_history"] = meta.get("chat_history", [])
    meta["chat_history"].append({"role": "user", "content": req.message})
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # Clear old build log for this iteration
    log_path = os.path.join(out_dir, "build.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"[Chat] User: {req.message}\n")

    # Call builder with resume mode
    async with httpx.AsyncClient(timeout=300.0) as client:
        await client.post(f"{BUILDER_URL}/chat", json={
            "id": widget_id,
            "message": req.message,
            "session_id": meta.get("session_id"),  # May be None for first chat
            "instrument": meta.get("instrument", "")
        })

    return {"success": True}
