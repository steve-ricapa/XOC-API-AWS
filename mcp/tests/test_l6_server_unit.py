"""Private snapshot refresh runs on the real loop without granting authority."""
import asyncio
import socket
import time
from types import SimpleNamespace

from xoc_mcp_lab_l6.control import ControlChannel
from xoc_mcp_lab_l6.pool import BoundedPool
from xoc_mcp_lab_l6.server import control_loop


def test_snapshot_request_returns_fresh_ordinary_heartbeat_without_probe_or_admission():
    async def scenario():
        left, right = socket.socketpair()
        supervisor = ControlChannel(left, generation=1)
        child = ControlChannel(right, generation=1)
        pool = BoundedPool(generation=1)
        observations = []
        app = SimpleNamespace(pool=pool, generation=1, fault_profile="none",
            probe_attempted=False, probe_ok=False, _observe=observations.append,
            snapshot=lambda: {"pool": pool.snapshot()})
        server = SimpleNamespace(should_exit=False)
        task = asyncio.create_task(control_loop(app, child, server))

        async def heartbeat(timeout):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                for message in supervisor.receive():
                    assert message["kind"] == "heartbeat", "snapshot refresh must not invoke a probe or ACK an open"
                    return message
                await asyncio.sleep(.001)
            raise AssertionError("LAB_FRESH_HEARTBEAT_NOT_RECEIVED")

        try:
            initial = await heartbeat(.2)
            before = pool.snapshot()
            issued_at = time.monotonic()
            assert issued_at - initial["heartbeat"] < .05
            assert supervisor.send("snapshot_request") is not None
            fresh = await heartbeat(.15)  # Strictly before the .25 periodic tick.
            assert fresh["heartbeat"] >= issued_at
            assert fresh["heartbeat"] > initial["heartbeat"]
            assert fresh["gate_state"] == "CLOSED"
            assert fresh["occupancy"] == 0 and fresh["jobs"] == []
            after = pool.snapshot()
            assert after["submitted"] == before["submitted"] == 0
            assert after["completed"] == before["completed"] == 0
            assert after["permits"] == before["permits"] == 4
            assert after["lease_until"] == before["lease_until"] == 0
            assert not app.probe_attempted and not app.probe_ok
            assert not pool.gate.opened_once
            assert [row["event"] for row in observations] == ["control_snapshot", "control_snapshot"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, .2)
            pool.shutdown(wait=True)
            child.close()
            supervisor.close()

    asyncio.run(scenario())
