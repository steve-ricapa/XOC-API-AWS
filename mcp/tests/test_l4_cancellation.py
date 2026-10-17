"""Finite worker cleanup, true disconnect and stateless cancellation over real HTTP."""
import asyncio
import copy
import gc
import json
import threading
import time
import weakref

import httpx
import pytest

from xoc_mcp_lab_l4.client import QueryClient, headers_for
from xoc_mcp_lab_l4.harness import ServerProcess, TOOLS, check_result, make_envelope, query


@pytest.fixture(autouse=True)
def installed_only(request):
    from xoc_mcp_lab.installed import is_installed
    if "pool_retains_only_active_scope" not in request.node.name and not is_installed():
        pytest.skip("Real HTTP lifecycle tests require installed C1/L4 artifacts in Linux")


def matching(server, request_id, event):
    return [row for row in server.events()
            if row.get("request_id") == request_id and row.get("event") == event]


async def wait_matching(server, request_id, event, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rows = matching(server, request_id, event)
        if rows:
            return rows
        await asyncio.sleep(0.005)
    raise AssertionError("LAB_EXPECTED_SAFE_LIFECYCLE_EVENT")


def rpc_call(name, rpc_id=7):
    return {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


def http_headers(value):
    return dict(headers_for(value), Accept="application/json, text/event-stream",
                **{"Content-Type": "application/json"})


async def warm(server):
    await server.wait_ready()
    result = await query(server)
    check_result(result, "admin_tenant_a", TOOLS[0])


def test_l4_finite_timeout_retains_slot_b_works_then_worker_cleans_up():
    async def scenario():
        server = ServerProcess("mixed")
        try:
            await warm(server)
            value = make_envelope(prefix="fault-slow")
            request_id = value["request_id"]
            pending = asyncio.create_task(QueryClient(server.url).run(value, timeout=0.3))
            await server.wait_event(request_id)
            assert (await pending)["status"] == "timeout"
            assert not matching(server, request_id, "worker_finished")
            b = await query(server, "admin_tenant_b", TOOLS[3])
            check_result(b, "admin_tenant_b", TOOLS[3])
            assert not matching(server, request_id, "worker_finished")
            b_worker = (await wait_matching(server, b["request_id"], "worker_finished"))[-1]
            assert b_worker["pool"]["active"] >= 1
            completed = (await wait_matching(server, request_id, "worker_finished"))[-1]
            assert completed["worker_context_cleared"]
            assert completed["worker_elapsed_ns"] >= 1_500_000_000
            assert completed["handler_calls"] == completed["executor_calls"] == completed["reads"] == 1
            assert completed["pool"]["active"] == 0
            finished = (await wait_matching(server, request_id, "request_finished"))[-1]
            assert finished["context_cleared"]
            reused = await query(server, "user_a2", TOOLS[3])
            check_result(reused, "user_a2", TOOLS[3])
        finally:
            assert server.close()["process_absent"]
    asyncio.run(scenario())


def test_l4_cancel_notification_other_context_same_rpc_id_does_not_cancel_a_or_b():
    async def scenario():
        server = ServerProcess("mixed")
        try:
            await warm(server)
            a = make_envelope(prefix="fault-slow")
            b = make_envelope("admin_tenant_b", TOOLS[3])
            async with httpx.AsyncClient(trust_env=False, timeout=5, follow_redirects=False) as client:
                a_request = asyncio.create_task(client.post(server.url, headers=http_headers(a),
                    json=rpc_call(TOOLS[0], rpc_id=7)))
                await server.wait_event(a["request_id"])
                cancellation = {"jsonrpc": "2.0", "method": "notifications/cancelled",
                    "params": {"requestId": 7, "reason": "LAB_FIXTURE_CANCEL_OTHER_CONTEXT"}}
                cancelled = await client.post(server.url, headers=http_headers(b), json=cancellation)
                assert cancelled.status_code == 202
                assert not a_request.done()
                b_response = await client.post(server.url, headers=http_headers(b),
                    json=rpc_call(TOOLS[3], rpc_id=7))
                assert b_response.status_code == 200
                payload = b_response.json()["result"]["structuredContent"]
                assert payload["status"] == "allowed" and payload["data"]["count"] == 3
                assert not a_request.done()
                a_response = await a_request
                assert a_response.status_code == 200
                payload = a_response.json()["result"]["structuredContent"]
                assert payload["status"] == "allowed"
                assert all(row["ticket_id"].startswith("lab-ticket-a") for row in payload["data"]["tickets"])
            for value in (a, b):
                workers = await wait_matching(server, value["request_id"], "worker_finished")
                assert all(row["worker_context_cleared"] for row in workers)
            cancellation_events = [row for row in matching(server, b["request_id"], "request_finished")
                                   if row.get("method") == "notifications/cancelled"]
            assert len(cancellation_events) == 1
            assert cancellation_events[0]["handler_calls"] == cancellation_events[0]["reads"] == 0
        finally:
            assert server.close()["process_absent"]
    asyncio.run(scenario())


def test_l4_explicit_tcp_disconnect_does_not_reuse_a_scope_or_free_its_thread():
    async def scenario():
        server = ServerProcess("mixed")
        writer = None
        try:
            await warm(server)
            value = make_envelope(prefix="fault-slow")
            body = json.dumps(rpc_call(TOOLS[0]), separators=(",", ":")).encode("utf-8")
            _, writer = await asyncio.open_connection("127.0.0.1", server.port)
            headers = http_headers(value)
            headers.update(Host=f"127.0.0.1:{server.port}", **{
                "Content-Length": str(len(body)), "Connection": "close"})
            head = "POST /mcp HTTP/1.1\r\n" + "".join(f"{key}: {entry}\r\n" for key, entry in headers.items()) + "\r\n"
            writer.write(head.encode("ascii") + body)
            await writer.drain()
            await server.wait_event(value["request_id"])
            writer.close()
            await writer.wait_closed()
            writer = None
            assert not matching(server, value["request_id"], "worker_finished")
            b = await query(server, "admin_tenant_b", TOOLS[3])
            check_result(b, "admin_tenant_b", TOOLS[3])
            assert not matching(server, value["request_id"], "worker_finished")
            b_worker = (await wait_matching(server, b["request_id"], "worker_finished"))[-1]
            assert b_worker["pool"]["active"] >= 1
            completed = (await wait_matching(server, value["request_id"], "worker_finished"))[-1]
            assert completed["worker_context_cleared"] and completed["pool"]["active"] == 0
            assert completed["handler_calls"] == completed["reads"] == 1
            assert completed["audit_records"][0]["effective_tenant_id"] == 91001
            assert completed["audit_records"][0]["user_id"] == 92001
            check_result(await query(server, "user_a2", TOOLS[3]), "user_a2", TOOLS[3])
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            assert server.close()["process_absent"]
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
def test_l4_initialize_and_list_independently_require_current_verified_context(method):
    async def scenario():
        server = ServerProcess()
        try:
            await server.wait_ready()
            value = make_envelope()
            params = {"protocolVersion": "2025-11-25", "capabilities": {},
                      "clientInfo": {"name": "LAB_CONTEXT_TEST", "version": "1"}} if method == "initialize" else {}
            message = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                valid = await client.post(server.url, headers=http_headers(value), json=message)
                assert valid.status_code == 200 and "result" in valid.json()
                missing = http_headers(value)
                missing.pop("X-XOC-Lab-Envelope")
                assert (await client.post(server.url, headers=missing, json=message)).status_code == 401
                changed = copy.deepcopy(value)
                changed["request_id"] = "lab-not-the-signed-request"
                assert (await client.post(server.url, headers=http_headers(changed), json=message)).status_code == 401
                expired = make_envelope(patch={"iat": int(time.time())-5,
                    "exp": int(time.time())-1, "deadline": int(time.time())-2})
                assert (await client.post(server.url, headers=http_headers(expired), json=message)).status_code == 401
            served = [row for row in await wait_matching(server, value["request_id"], "request_finished")
                      if row.get("method") == method]
            assert len(served) == 1
            assert served[0]["context_cleared"]
            assert served[0]["handler_calls"] == served[0]["executor_calls"] == served[0]["reads"] == 0
            assert not any(row.get("event") == "provider_entered" for row in server.events())
        finally:
            assert server.close()["process_absent"]
    asyncio.run(scenario())


def test_l4_pool_retains_only_active_scope_then_drops_it_after_timeout():
    from xoc_mcp_lab.request_envelope import verify_envelope
    from xoc_mcp_lab_l4.pool import BoundedPool
    from xoc_mcp_lab_l4.scope import RequestScope, current_scope

    async def scenario():
        scope = RequestScope(verify_envelope(make_envelope()))
        reference = weakref.ref(scope)
        entered, release = threading.Event(), threading.Event()
        pool = BoundedPool()

        def blocking():
            current_scope().verified.require_valid()
            entered.set()
            release.wait()
            current_scope().verified.require_valid()
            return 1

        async def until_timeout(value):
            try:
                await pool.run(value, blocking, 0.05)
            except TimeoutError:
                return "timeout"

        try:
            assert await until_timeout(scope) == "timeout"
            assert entered.is_set() and pool.snapshot()["active"] == 1
            del scope
            gc.collect()
            assert reference() is not None  # Still legitimately owned by its real worker.
            release.set()
            end = time.monotonic() + 2
            while pool.snapshot()["active"]:
                assert time.monotonic() < end
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
            gc.collect()
            assert reference() is None
        finally:
            release.set()
            pool.shutdown(wait=True)
    asyncio.run(scenario())
