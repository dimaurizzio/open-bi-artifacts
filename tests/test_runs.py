import asyncio

from agent_dashboards.server.runs import RunManager, sse_events


async def _collect(run):
    return [item async for item in run.follow()]


def test_follow_replays_from_start_after_disconnect():
    async def scenario():
        manager = RunManager()

        async def produce(run):
            for i in range(3):
                await asyncio.sleep(0.01)
                await run.push({"type": "text", "n": i})

        run = manager.start("c1", [{"type": "user"}], produce)
        # Un lector que se corta enseguida no detiene el turno.
        first = run.follow()
        assert (await anext(first))["type"] == "user"
        await first.aclose()
        # Otro lector que llega tarde recibe todo desde el principio.
        items = await _collect(run)
        assert [i.get("n") for i in items] == [None, 0, 1, 2]
        assert manager.active("c1") is None and manager.get("c1") is run

    asyncio.run(scenario())


def test_cancel_marks_turn_cancelled():
    async def scenario():
        manager = RunManager()

        async def produce(run):
            await run.push({"type": "text"})
            await asyncio.sleep(10)

        run = manager.start("c1", [], produce)
        await asyncio.sleep(0.01)
        assert "c1" in manager.running_ids()
        assert manager.cancel("c1")
        items = await _collect(run)
        assert items[-1] == {"type": "cancelled"}
        assert manager.running_ids() == set()

    asyncio.run(scenario())


def test_one_turn_at_a_time_per_chat():
    async def scenario():
        manager = RunManager()

        async def produce(run):
            await asyncio.sleep(0.05)

        manager.start("c1", [], produce)
        try:
            manager.start("c1", [], produce)
        except RuntimeError:
            pass
        else:
            raise AssertionError("debió rechazar un segundo turno simultáneo")
        manager.start("c2", [], produce)  # otro chat sí puede correr en paralelo
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_errors_reach_the_stream():
    async def scenario():
        manager = RunManager()

        async def produce(run):
            raise ValueError("falló BigQuery")

        items = await _collect(manager.start("c1", [], produce))
        assert items[-1]["type"] == "error" and "falló BigQuery" in items[-1]["message"]

    asyncio.run(scenario())


def test_sse_pings_while_the_turn_is_quiet():
    async def scenario():
        manager = RunManager()
        release = asyncio.Event()

        async def produce(run):
            await release.wait()
            await run.push({"type": "text", "n": 1})

        run = manager.start("c2", [{"type": "user"}], produce)
        events = sse_events(run, ping_seconds=0.02)
        assert (await anext(events)).startswith("data: ")  # el mensaje del usuario
        assert await anext(events) == ": ping\n\n"  # turno callado: comentario SSE
        release.set()
        rest = [e async for e in events]
        data = [e for e in rest if e.startswith("data: ")]
        assert data[0].startswith('data: {"type": "text", "n": 1}') and data[-1] == 'data: {"type": "done"}\n\n'

    asyncio.run(scenario())
