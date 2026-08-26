"""Cloud-hosted MCP gateway for Megatron.

This is intentionally separate from the original desktop Xiaozhi broker.
It exposes an inbound WebSocket MCP endpoint that Xiaozhi can connect to,
while reusing the existing Megatron tool implementations and Render API.

Cloud-safe tools are advertised; Windows-only desktop-control tools are not.
"""

import asyncio
import json
import os
import traceback
from typing import Any

# The legacy desktop broker requires XIAOZHI_MCP_URL at import time.
# Cloud mode does not dial Xiaozhi, so provide a harmless placeholder.
os.environ.setdefault("XIAOZHI_MCP_URL", "ws://127.0.0.1:9")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# Reuse the existing tool implementations.
# Importing the existing file does not start its main() loop.
import mcp_server as core

app = FastAPI(title="Megatron Cloud MCP", version="1.0.0")

# PC / Windows-only and local-audio tools are deliberately excluded.
CLOUD_SAFE_TOOLS = {
    "ask_megatron",
    "get_status",
    "get_time",
    "calculate",
    "math_brain",
    "converter",
    "smart_explain",
    "smart_intent",
    "smart_quiz",
    "smart_reminder",
    "search_web",
    "news_briefing",
}

CLOUD_TOOLS = [
    tool for tool in core.TOOLS
    if tool.get("name") in CLOUD_SAFE_TOOLS
]


def rpc_result(request_id: Any, result: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    error = {
        "code": code,
        "message": message,
    }
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def cloud_time_result() -> dict:
    # Keep Megatron's expected India-centric voice behavior even on a UTC cloud VM.
    from datetime import datetime, timezone, timedelta

    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    text = now.strftime("%I:%M %p IST | %d %B %Y")
    return {"content": [{"type": "text", "text": text}]}


async def execute_cloud_tool(name: str, arguments: dict) -> dict:
    if name not in CLOUD_SAFE_TOOLS:
        return {
            "content": [{
                "type": "text",
                "text": f"Tool '{name}' is not available in cloud mode.",
            }],
            "isError": True,
        }

    if name == "get_time":
        return cloud_time_result()

    # Keep the exact smart routing behavior from the current MCP implementation.
    routed_name, routed_args, routed = core.route_local_intent(
        name,
        arguments,
    )

    if routed_name not in CLOUD_SAFE_TOOLS:
        # A desktop-specific route must never execute on the cloud.
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Cloud mode does not support desktop tool '{routed_name}'."
                ),
            }],
            "isError": True,
        }

    result = await core._execute_tool(
        routed_name,
        routed_args,
    )

    if result is None:
        return {
            "content": [{
                "type": "text",
                "text": "Tool returned no result.",
            }],
            "isError": True,
        }

    return result


@app.get("/")
async def root():
    return {
        "service": "Megatron Cloud MCP",
        "status": "online",
        "transport": "websocket",
        "websocket": "/mcp",
        "tools": len(CLOUD_TOOLS),
        "api_url": os.getenv(
            "MEGATRON_API_URL",
            "not configured",
        ),
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "megatron-cloud-mcp",
        "tools": len(CLOUD_TOOLS),
    }


@app.websocket("/mcp")
async def mcp_websocket(websocket: WebSocket):
    await websocket.accept()

    client_name = "unknown"

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                message = json.loads(raw)
            except json.JSONDecodeError as error:
                await websocket.send_text(
                    json.dumps(
                        rpc_error(None, -32700, "Parse error", str(error)),
                        ensure_ascii=False,
                    )
                )
                continue

            method = message.get("method")
            request_id = message.get("id")
            params = message.get("params") or {}

            if method == "initialize":
                info = params.get("clientInfo") or {}
                client_name = info.get("name", "unknown")

                response = rpc_result(
                    request_id,
                    {
                        "protocolVersion": params.get(
                            "protocolVersion",
                            "2024-11-05",
                        ),
                        "capabilities": {
                            "tools": {},
                        },
                        "serverInfo": {
                            "name": "Megatron Cloud MCP",
                            "version": "1.0.0",
                        },
                    },
                )
                await websocket.send_text(
                    json.dumps(response, ensure_ascii=False)
                )
                print(f"[MCP] initialized client={client_name}")
                continue

            if method == "notifications/initialized":
                print(f"[MCP] client initialized: {client_name}")
                continue

            if method == "ping":
                await websocket.send_text(
                    json.dumps(
                        rpc_result(request_id, {}),
                        ensure_ascii=False,
                    )
                )
                continue

            if method == "tools/list":
                await websocket.send_text(
                    json.dumps(
                        rpc_result(
                            request_id,
                            {"tools": CLOUD_TOOLS},
                        ),
                        ensure_ascii=False,
                    )
                )
                continue

            if method == "tools/call":
                tool_name = str(
                    params.get("name", "")
                ).strip()
                arguments = params.get("arguments") or {}

                if tool_name not in CLOUD_SAFE_TOOLS:
                    await websocket.send_text(
                        json.dumps(
                            rpc_error(
                                request_id,
                                -32601,
                                f"Tool '{tool_name}' is not available in cloud mode.",
                            ),
                            ensure_ascii=False,
                        )
                    )
                    continue

                try:
                    print(
                        f"[CLOUD MCP] tool={tool_name} args={arguments}"
                    )
                    result = await asyncio.wait_for(
                        execute_cloud_tool(
                            tool_name,
                            arguments,
                        ),
                        timeout=120.0,
                    )
                    await websocket.send_text(
                        json.dumps(
                            rpc_result(request_id, result),
                            ensure_ascii=False,
                        )
                    )
                except asyncio.TimeoutError:
                    await websocket.send_text(
                        json.dumps(
                            rpc_error(
                                request_id,
                                -32000,
                                "Tool execution timed out.",
                            ),
                            ensure_ascii=False,
                        )
                    )
                except Exception as error:
                    traceback.print_exc()
                    await websocket.send_text(
                        json.dumps(
                            rpc_error(
                                request_id,
                                -32603,
                                "Tool execution failed.",
                                str(error),
                            ),
                            ensure_ascii=False,
                        )
                    )
                continue

            # Unknown request.
            await websocket.send_text(
                json.dumps(
                    rpc_error(
                        request_id,
                        -32601,
                        f"Method '{method}' not found.",
                    ),
                    ensure_ascii=False,
                )
            )

    except WebSocketDisconnect:
        print(
            f"[MCP] client disconnected: {client_name}"
        )
    except Exception as error:
        print(
            f"[MCP] websocket error: {error}"
        )
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "cloud_mcp_server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
