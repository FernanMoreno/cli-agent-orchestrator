"""Actual MCP process and TCP HTTP hop into the production PostgreSQL ledger.

No provider CLI is launched. This verifies the bundled MCP protocol boundary;
it must not be reported as provider or browser acceptance evidence.
"""

import asyncio
import json
import os
import socket
import sys
import threading
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.postgres


def _result(result):
    assert not result.isError
    if result.structuredContent is not None:
        value = result.structuredContent
        return value.get("result", value)
    return json.loads(next(item.text for item in result.content if item.type == "text"))


@pytest.mark.asyncio
async def test_real_stdio_ask_answer_wait_ack_over_tcp(interaction_http_context, tmp_path):
    from cli_agent_orchestrator.api.interactions import router
    from cli_agent_orchestrator.security.auth import get_current_principal

    c = interaction_http_context
    app = FastAPI()
    app.state.interaction_service = c.service
    app.dependency_overrides[get_current_principal] = lambda: c.owner
    app.include_router(router)

    @app.get("/terminals/{terminal_id}")
    def terminal_context(terminal_id: str):
        assert terminal_id == c.terminal_id
        return {
            "id": terminal_id,
            "session_name": c.session_name,
            "provider": "codex",
            "allowed_tools": ["@cao-mcp-server"],
        }

    @app.get("/terminals/{terminal_id}/working-directory")
    def working_directory(terminal_id: str):
        return {"working_directory": str(tmp_path)}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))

    def run_server():
        asyncio.run(server.serve(sockets=[listener]))

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        env = dict(os.environ)
        env.update(
            CAO_HOME_DIR=str(tmp_path / "stdio-cao"),
            CAO_API_HOST="127.0.0.1",
            CAO_API_PORT=str(port),
            CAO_TERMINAL_ID=c.terminal_id,
            CAO_INTERACTION_CAPABILITY_FILE=str(c.capability_file),
        )

        async def journey():
            async with stdio_client(
                StdioServerParameters(
                    command=sys.executable,
                    args=[
                        "-c",
                        "from cli_agent_orchestrator.mcp_server.server import main; main()",
                    ],
                    env=env,
                )
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    available = {tool.name for tool in (await session.list_tools()).tools}
                    assert {
                        "cao_ask_user",
                        "cao_wait_for_user_answers",
                        "cao_acknowledge_user_answers",
                    } <= available
                    created = _result(
                        await session.call_tool(
                            "cao_ask_user",
                            {
                                "questions": [
                                    {"kind": "text", "prompt": "Which format?", "required": True}
                                ],
                                "operation_key": "real-stdio-question-operation",
                            },
                        )
                    )
                    assert "group_id" in created, created
                    q = created["questions"][0]
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as human:
                        answer = await human.post(
                            f"/interactions/{q['question_id']}/answers",
                            headers={
                                "If-Match": f'"{q["version"]}"',
                                "Idempotency-Key": "real-stdio-answer-operation",
                            },
                            json={
                                "expected_version": q["version"],
                                "response": {"kind": "text", "text": "JSON"},
                            },
                        )
                        assert answer.status_code == 201, answer.text
                    ready = _result(
                        await session.call_tool(
                            "cao_wait_for_user_answers",
                            {
                                "group_id": created["group_id"],
                                "wait_seconds": 0,
                            },
                        )
                    )
                    assert ready["state"] == "ready", ready
                    assert ready["answers"][0]["response"] == {"kind": "text", "text": "JSON"}
                    acknowledged = _result(
                        await session.call_tool(
                            "cao_acknowledge_user_answers",
                            {
                                "group_id": created["group_id"],
                                "receipts": [
                                    {
                                        key: ready["answers"][0][key]
                                        for key in ("question_id", "version", "receipt")
                                    }
                                ],
                            },
                        )
                    )
                    assert acknowledged == {"success": True, "acknowledged": 1}
                    with c.service.repository.read_connection() as connection:
                        assert (
                            connection.execute(
                                "SELECT state FROM interaction_deliveries"
                            ).fetchone()[0]
                            == "agent_acknowledged"
                        )

        await asyncio.wait_for(journey(), timeout=90)
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 5)
        listener.close()
