"""Realtime dashboard WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import SessionLocal
from app.services.broadcast import Client, broadcaster
from app.services.camera_manager import camera_manager
from app.services.events import list_events

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token") or websocket.query_params.get("access_token")
    topics_param = websocket.query_params.get("topics", "*")
    if not token:
        await websocket.close(code=4401, reason="Authentication required")
        return
    try:
        payload = decode_access_token(token)
    except Exception:  # noqa: BLE001
        await websocket.close(code=4401, reason="Invalid or expired token")
        return
    username = str(payload.get("sub", ""))
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None or not user.is_active:
            await websocket.close(code=4403, reason="User disabled")
            return
        role = user.role

    topics = set(t.strip() for t in topics_param.split(",") if t.strip()) or {"*"}
    client = Client(topics=topics, username=username, role=role)
    await websocket.accept()
    broadcaster.bind_loop(asyncio.get_running_loop())
    broadcaster.register(client)

    async def sender() -> None:
        while True:
            message = await client.queue.get()
            if message is None:
                break
            await websocket.send_text(json.dumps(message, default=str))

    send_task = asyncio.create_task(sender())
    try:
        # Initial state so the dashboard paints immediately after (re)connect.
        await websocket.send_text(
            json.dumps(
                {
                    "topic": "hello",
                    "type": "connected",
                    "user": {"username": username, "role": role},
                    "topics": sorted(topics),
                    "cameras": camera_manager.all_status(),
                    "recent_events": list_events(limit=25),
                    "recent_alerts": broadcaster.recent()[-25:],
                    "server_time": __import__("time").time(),
                },
                default=str,
            )
        )
        while True:
            raw = await websocket.receive_text()
            try:
                message: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if message.get("type") == "ping":
                await websocket.send_text(json.dumps({"topic": "pong", "type": "pong", "t": message.get("t")}))
            elif message.get("type") == "subscribe" and isinstance(message.get("topics"), list):
                client.topics = set(str(t) for t in message["topics"]) or {"*"}
                await websocket.send_text(
                    json.dumps({"topic": "ack", "type": "subscribed", "topics": sorted(client.topics)})
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("WebSocket closed: %s", exc)
    finally:
        broadcaster.unregister(client)
        client.queue.put_nowait(None)
        send_task.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
