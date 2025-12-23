import os
import json
import uuid
import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional

BUILDER_URL = os.getenv("BUILDER_URL", "http://builder:8090")
WIDGETS_DIR = os.getenv("WIDGETS_DIR", "/data/widgets")
DERIBIT_API = "https://www.deribit.com/api/v2"

router = APIRouter()


# ============ Deribit Data API ============

@router.get("/instruments")
async def get_instruments(
    currency: str = Query("BTC", description="Currency: BTC, ETH, SOL, etc."),
    kind: str = Query("option", description="Kind: future, option, spot, all"),
    expired: bool = Query(False, description="Include expired instruments"),
):
    """
    获取 Deribit 合约列表。

    返回格式:
    ```json
    {
      "instruments": [
        {
          "instrument_name": "BTC-26DEC25-100000-C",
          "kind": "option",
          "option_type": "call",  // call 或 put（仅期权）
          "strike": 100000,       // 行权价（仅期权）
          "expiration_timestamp": 1766390400000,
          "is_active": true
        },
        ...
      ],
      "currency": "BTC",
      "kind": "option"
    }
    ```
    """
    params = {"currency": currency, "expired": str(expired).lower()}
    if kind != "all":
        params["kind"] = kind

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{DERIBIT_API}/public/get_instruments", params=params)
        resp.raise_for_status()
        data = resp.json()

    instruments = data.get("result", [])

    # 简化返回结构，只保留关键字段
    simplified = []
    for inst in instruments:
        item = {
            "instrument_name": inst["instrument_name"],
            "kind": inst["kind"],
            "expiration_timestamp": inst.get("expiration_timestamp"),
            "is_active": inst.get("is_active", True),
        }
        if inst["kind"] == "option":
            item["option_type"] = inst.get("option_type")
            item["strike"] = inst.get("strike")
        simplified.append(item)

    return {
        "instruments": simplified,
        "currency": currency,
        "kind": kind,
        "count": len(simplified),
    }


@router.get("/instruments/summary")
async def get_instruments_summary(
    currency: str = Query("BTC", description="Currency: BTC, ETH, SOL, etc."),
    kind: str = Query("option", description="Kind: future, option, spot"),
):
    """
    获取合约摘要（含 IV、价格等），但不含 Greeks。

    适用于期权链概览、快速筛选等场景。

    返回格式:
    ```json
    {
      "instruments": [
        {
          "instrument_name": "BTC-26DEC25-100000-C",
          "mark_price": 0.0015,
          "mark_iv": 47.31,           // 隐含波动率 (%)
          "underlying_price": 89026.51,
          "bid_price": 0.0015,
          "ask_price": 0.0017,
          "open_interest": 1315.3,
          "volume_usd": 12345.67
        },
        ...
      ]
    }
    ```

    注意：此接口不返回 Greeks (delta, gamma 等)。
    如需 Greeks，请订阅 WebSocket ticker 频道。
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{DERIBIT_API}/public/get_book_summary_by_currency",
            params={"currency": currency, "kind": kind}
        )
        resp.raise_for_status()
        data = resp.json()

    instruments = data.get("result", [])

    # 简化返回结构
    simplified = []
    for inst in instruments:
        simplified.append({
            "instrument_name": inst["instrument_name"],
            "mark_price": inst.get("mark_price"),
            "mark_iv": inst.get("mark_iv"),
            "underlying_price": inst.get("underlying_price"),
            "underlying_index": inst.get("underlying_index"),
            "bid_price": inst.get("bid_price"),
            "ask_price": inst.get("ask_price"),
            "mid_price": inst.get("mid_price"),
            "open_interest": inst.get("open_interest"),
            "volume_usd": inst.get("volume_usd"),
        })

    return {
        "instruments": simplified,
        "currency": currency,
        "kind": kind,
        "count": len(simplified),
    }


@router.get("/instruments/expirations")
async def get_expirations(
    currency: str = Query("BTC", description="Currency: BTC, ETH, SOL, etc."),
):
    """
    获取所有期权到期日列表（按时间排序）。

    返回格式:
    ```json
    {
      "expirations": [
        {
          "timestamp": 1766390400000,
          "date": "2025-12-26",
          "label": "26DEC25"
        },
        ...
      ]
    }
    ```
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{DERIBIT_API}/public/get_instruments",
            params={"currency": currency, "kind": "option", "expired": "false"}
        )
        resp.raise_for_status()
        data = resp.json()

    instruments = data.get("result", [])

    # 提取 unique 到期日
    expirations_set = set()
    for inst in instruments:
        ts = inst.get("expiration_timestamp")
        if ts:
            expirations_set.add(ts)

    # 排序并格式化
    expirations = []
    for ts in sorted(expirations_set):
        from datetime import datetime
        dt = datetime.utcfromtimestamp(ts / 1000)
        expirations.append({
            "timestamp": ts,
            "date": dt.strftime("%Y-%m-%d"),
            "label": dt.strftime("%d%b%y").upper(),  # e.g., "26DEC25"
        })

    return {
        "expirations": expirations,
        "currency": currency,
        "count": len(expirations),
    }


# ============ Widget API ============


class CreateWidgetReq(BaseModel):
    prompt: str


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
        "status": "queued",
    }
    meta_path = os.path.join(out_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    async with httpx.AsyncClient(timeout=300.0) as client:
        await client.post(f"{BUILDER_URL}/build", json={
            "id": wid,
            "prompt": req.prompt,
        })

    return {
        "id": wid,
        "widget_url": f"/widgets/{wid}/dist/",
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


@router.delete("/widgets/{widget_id}")
async def delete_widget(widget_id: str):
    """Delete a widget and all its files."""
    import shutil
    widget_dir = os.path.join(WIDGETS_DIR, widget_id)
    if not os.path.exists(widget_dir):
        return {"error": "Widget not found"}

    shutil.rmtree(widget_dir)
    return {"success": True, "id": widget_id}


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
        })

    return {"success": True}
