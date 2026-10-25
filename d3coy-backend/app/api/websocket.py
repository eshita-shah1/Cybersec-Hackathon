"""WebSocket endpoint /ws/events.

Message envelope (server -> client):
    {"type": "connected" | "security_event" | "security_alert" | "alert_update" | "agent_status" | "pong",
     "version": 1, "sent_at": "<ISO-8601 UTC>", "data": {...}}

``security_event`` data: event_id, agent_id, timestamp, tool_name, action, target_resource,
policy_status, risk_score, risk_level, decision, request_status, execution_status,
explanations, review_id, alert_ids, escalation.

Clients may send the text "ping" and receive a ``pong`` message.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/events")
async def events_socket(ws: WebSocket) -> None:
    manager = ws.app.state.container.ws
    client = await manager.connect(ws)
    try:
        while True:
            text = await ws.receive_text()
            if text.strip().lower() == "ping":
                await client.queue.put(manager.envelope("pong", {}))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.info("WebSocket client %s closed with an error", client.id)
    finally:
        await manager.disconnect(client)
