"""A slow filesystem probe must not stop unrelated event-loop work."""

import asyncio
import threading

import httpx

from cli_agent_orchestrator.api import main


def test_slow_health_path_probe_does_not_block_event_loop(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_which(_binary):
        entered.set()
        release.wait(2)
        return None

    monkeypatch.setattr("shutil.which", slow_which)
    monkeypatch.setattr(main, "get_backend", lambda: object())

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            request = asyncio.create_task(client.get("/health"))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                responsive = not request.done()
            finally:
                release.set()
                response = await request
            assert response.status_code == 200
            assert response.json()["components"]["claude"] == "unavailable"
            assert responsive, "PATH filesystem probes ran on the HTTP event loop"

    asyncio.run(scenario())
