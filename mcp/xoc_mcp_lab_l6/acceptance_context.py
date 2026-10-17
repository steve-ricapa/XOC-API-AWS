"""C09/C10/C13/C21/C22: real HTTP contexts; only fictional signed inputs."""
from __future__ import annotations

import asyncio
import json
import time
from uuid import uuid4

import httpx

from xoc_mcp_lab_l4.client import QueryClient, headers_for
from .harness import TOOLS, check_result, make_envelope, query, restored, safe_row


def headers(value):
    return dict(headers_for(value), Accept="application/json, text/event-stream",
                **{"Content-Type": "application/json"})


def rpc(name=TOOLS[0], rpc_id=7, arguments=None):
    return dict(jsonrpc="2.0", id=rpc_id, method="tools/call",
                params=dict(name=name, arguments={} if arguments is None else arguments))


def matching(server, request_id, event):
    return [row for row in server.events() if row.get("request_id") == request_id and row.get("event") == event]


async def finished(server, request_id):
    # ClientSession may emit a technical tools/list for output-schema checking
    # after tools/call. Both consume slots; file flush order is not authority.
    until = time.monotonic() + 4
    while True:
        business = [row for row in matching(server, request_id, "worker_finished")
                    if row.get("handler_calls") == 1]
        if business:
            assert len(business) == 1, "LAB_L6_UNEXPECTED_BUSINESS_REEXECUTION"
            row = business[0]
            break
        assert time.monotonic() < until, "LAB_L6_EXPECTED_BUSINESS_WORKER_COMPLETION"
        await asyncio.sleep(.005)
    assert row["worker_context_cleared"]
    assert row["callable_finished_at"] <= row["context_reset_at"] <= row["future_done_at"] <= row["slot_released_at"]
    return row


async def check_b_while_a(server, a_id):
    assert not matching(server, a_id, "worker_finished")
    b = await query(server, "admin_tenant_b", TOOLS[3])
    check_result(b, "admin_tenant_b", TOOLS[3])
    worker = await finished(server, b["request_id"])
    assert worker["pool"]["active"] >= 1
    assert not matching(server, a_id, "worker_finished")
    return safe_row(b)


async def c09(server):
    generation = server.generation
    rows = []
    # Local timeout is not assumed to be remotely observed cancellation.
    a = make_envelope(prefix="fault-slow")
    task = asyncio.create_task(QueryClient(server.url).run(a, timeout=.3))
    await server.wait_event(a["request_id"])
    caller = await task
    assert caller["status"] == "timeout"
    rows.append(dict(mode="caller_timeout", caller=safe_row(caller),
                     b=await check_b_while_a(server, a["request_id"]),
                     worker=await finished(server, a["request_id"])))

    a = make_envelope(prefix="fault-slow")
    task = asyncio.create_task(QueryClient(server.url).run(a))
    await server.wait_event(a["request_id"])
    cancelled_at = time.monotonic()
    task.cancel()
    try:
        await task
        raise AssertionError("LAB_CANCEL_DID_NOT_REACH_CALLER")
    except asyncio.CancelledError:
        pass
    rows.append(dict(mode="cancel_caller_task", cancelled_at=cancelled_at,
                     b=await check_b_while_a(server, a["request_id"]),
                     worker=await finished(server, a["request_id"])))

    a = make_envelope(prefix="fault-slow")
    body = json.dumps(rpc(), separators=(",", ":")).encode()
    _, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        head = headers(a)
        head.update(Host=f"127.0.0.1:{server.port}", **{"Content-Length": str(len(body)), "Connection": "close"})
        wire = "POST /mcp HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in head.items()) + "\r\n"
        writer.write(wire.encode("ascii") + body)
        await writer.drain()
        await server.wait_event(a["request_id"])
        disconnected_at = time.monotonic()
        writer.close()
        await writer.wait_closed()
        rows.append(dict(mode="explicit_tcp_disconnect", disconnected_at=disconnected_at,
                         b=await check_b_while_a(server, a["request_id"]),
                         worker=await finished(server, a["request_id"])))
    finally:
        writer.close()
        await writer.wait_closed()

    a = make_envelope(prefix="fault-slow")
    b = make_envelope("admin_tenant_b", TOOLS[3])
    async with httpx.AsyncClient(trust_env=False, timeout=3.5, follow_redirects=False) as client:
        task = asyncio.create_task(client.post(server.url, headers=headers(a), json=rpc()))
        await server.wait_event(a["request_id"])
        notification = dict(jsonrpc="2.0", method="notifications/cancelled", params={"requestId": 7})
        response = await client.post(server.url, headers=headers(b), json=notification)
        assert response.status_code == 202 and not task.done()
        healthy = await check_b_while_a(server, a["request_id"])
        result = await task
        payload = result.json()["result"]["structuredContent"]
        assert result.status_code == 200
        check_result(dict(status=payload["status"], result=payload), "admin_tenant_a", TOOLS[0])
    notice = [row for row in (await server.wait_event(b["request_id"], event="request_finished"))
              if row.get("method") == "notifications/cancelled"]
    assert len(notice) == 1 and notice[0]["handler_calls"] == notice[0]["reads"] == 0
    rows.append(dict(mode="stateless_notification", http_status=response.status_code,
                     b=healthy, worker=await finished(server, a["request_id"]), notification=notice[0]))
    status = await server.wait_idle()
    assert status["child_generation"] == generation and status["attempt"] == 0
    return dict(subcases=rows, final=status, limits="Remote abandonment is not inferred from a local timeout.")


async def c10(server):
    generation = server.generation
    shared_id = "lab-fault-block-shared-" + uuid4().hex[:10]
    a = make_envelope("admin_tenant_a", TOOLS[0], patch={"request_id": shared_id})
    b = make_envelope("admin_tenant_b", TOOLS[0], patch={"request_id": shared_id})
    async with httpx.AsyncClient(trust_env=False, timeout=3.5, follow_redirects=False) as client:
        a_task = asyncio.create_task(client.post(server.url, headers=headers(a), json=rpc(rpc_id=91)))
        b_task = asyncio.create_task(client.post(server.url, headers=headers(b), json=rpc(rpc_id=91)))
        until = time.monotonic() + 3
        while len(matching(server, shared_id, "provider_entered")) < 2:
            assert time.monotonic() < until
            await asyncio.sleep(.005)
        starts = matching(server, shared_id, "provider_entered")
        assert len({row["internal_admission_id"] for row in starts}) == 2
        assert {row["fixture_effective_tenant_id"] for row in starts} == {91001, 91002}
        b_task.cancel()
        try:
            await b_task
        except asyncio.CancelledError:
            pass
        response = await client.post(server.url, headers=headers(b), json=dict(jsonrpc="2.0",
            method="notifications/cancelled", params={"requestId": 91}))
        assert response.status_code == 202
        assert not matching(server, shared_id, "worker_finished")
        try:
            a_response = await a_task
            assert "error" in a_response.json() or a_response.status_code != 200
            a_outcome = {"http_status": a_response.status_code, "error": True}
        except httpx.HTTPError as error:
            a_outcome = {"error": True, "type": type(error).__name__}
    restoration = await restored(server, generation + 1, fault_at=min(row["monotonic_ns"] for row in starts) / 1e9)
    after = []
    for profile in ("admin_tenant_a", "admin_tenant_b", "user_a2"):
        result = await query(server, profile, TOOLS[3])
        check_result(result, profile, TOOLS[3])
        after.append(safe_row(result))
    # Fresh requests prove no old context is restored; no original request is replayed.
    return dict(shared_request_id=shared_id, rpc_id=91, started=starts,
                cancelled_b=True, a=a_outcome, restoration=restoration, after=after)


async def c13(server):
    generation = server.generation
    before = len(server.events())
    rows, healthy = [], []
    async with httpx.AsyncClient(trust_env=False, timeout=3.5, follow_redirects=False,
            limits=httpx.Limits(max_connections=16)) as client:
        async def invalid(index):
            kind = index % 3
            value = make_envelope("superadmin" if kind == 1 else "admin_tenant_a",
                                  args={"tenantId": 91002} if kind == 2 else {})
            if kind == 0:
                pieces = value["context_assertion"].split(".")
                pieces[2] = ("B" if pieces[2][0] != "B" else "C") + pieces[2][1:]
                value["context_assertion"] = ".".join(pieces)
            began = time.monotonic()
            response = await client.post(server.url, headers=headers(value), json=rpc(arguments=value["tool"]["arguments"]))
            payload = response.json()
            assert response.status_code != 200 or payload.get("error") or payload["result"]["structuredContent"]["status"] != "allowed"
            return dict(request_id=value["request_id"], kind=("signature", "policy_deny", "ownership")[kind],
                        http_status=response.status_code, seconds=time.monotonic()-began)

        for offset in range(0, 256, 16):
            rows.extend(await asyncio.gather(*(invalid(index) for index in range(offset, offset+16))))
            result = await query(server, "admin_tenant_b", TOOLS[3])
            check_result(result, "admin_tenant_b", TOOLS[3])
            healthy.append(safe_row(result))
    ids = {row["request_id"] for row in rows}
    events = [row for row in server.events()[before:] if row.get("request_id") in ids]
    assert all(row.get("handler_calls", 0) == row.get("reads", 0) == 0 for row in events)
    status = await server.wait_idle()
    assert status["child_generation"] == generation and status["attempt"] == 0
    return dict(attempts=rows, count=len(rows), max_concurrency=16, b_interleaved=healthy,
                invalid_handlers=0, final=status, limited_load_not_dos_immunity=True)


async def c21(server):
    generation = server.generation
    rows = []
    async with httpx.AsyncClient(trust_env=False, timeout=3.5, follow_redirects=False) as client:
        for method in ("initialize", "tools/list", "tools/call"):
            now = int(time.time())
            value = make_envelope(patch=dict(iat=now-5, exp=now-1, deadline=now-2))
            params = dict(protocolVersion="2025-11-25", capabilities={},
                          clientInfo=dict(name="LAB_L6_EXPIRY", version="1")) if method == "initialize" else {}
            message = rpc() if method == "tools/call" else dict(jsonrpc="2.0", id=7, method=method, params=params)
            response = await client.post(server.url, headers=headers(value), json=message)
            assert response.status_code == 401
            assert not matching(server, value["request_id"], "provider_entered")
            rows.append(dict(method=method, http_status=response.status_code, request_id=value["request_id"]))
        # A deadline that expires after actual provider entry never frees its slot.
        now = int(time.time())
        # Even at the very end of a wall-clock second there is >1 second
        # to enter the provider. The finite four-second job then outlives
        # its signed deadline but remains below the five-second work limit.
        a = make_envelope(prefix="fault-long", patch=dict(iat=now, exp=now+5, deadline=now+2))
        task = asyncio.create_task(client.post(server.url, headers=headers(a), json=rpc()))
        await server.wait_event(a["request_id"])
        response = await task
        payload = response.json()
        assert response.status_code != 200 or "error" in payload or payload["result"]["structuredContent"]["status"] != "allowed"
        assert not matching(server, a["request_id"], "worker_finished")
        b = await check_b_while_a(server, a["request_id"])
        actual_end = await finished(server, a["request_id"])
        assert actual_end["handler_calls"] == 1
    status = await server.wait_idle()
    assert status["child_generation"] == generation and status["attempt"] == 0
    return dict(expired_before_entry=rows, started_then_expired=dict(request_id=a["request_id"],
                http_status=response.status_code, b=b, worker=actual_end), final=status,
                deterministic_gate_submit_start_races="test_l6_pool_unit.py:test_C21_revalidate_after_submit_before_worker_execution")


async def c22(server):
    generation = server.generation
    probes = [row for row in server.events(generation) if row.get("event") == "private_probe_finished"]
    assert len(probes) == 1 and probes[0]["ok"] and probes[0]["occupancy"] == 0
    assert probes[0]["slot_released_at"] <= probes[0]["finished_at"]
    status = await server.status()
    # Once renewals are paused, the maximum existing absolute lease is observed.
    index = len(server.messages)
    server.command("test", action="pause_lease", duration=2)
    await asyncio.sleep(.04)
    status = await server.status()
    lease_until = status["lease_until"]
    await asyncio.sleep(max(0, lease_until-time.monotonic()) + .015)
    attempts = []
    async with httpx.AsyncClient(trust_env=False, timeout=1, follow_redirects=False) as client:
        async def attempt(index):
            value = make_envelope(prefix="probe-forgery")
            request_headers = headers(value)
            message = rpc(rpc_id="private-probe")
            if index == 0:
                request_headers["X-XOC-Private-Probe"] = "true"
            elif index == 1:
                request_headers["X-XOC-L6-Private-Capability"] = "true"
            elif index == 2:
                message["params"]["_meta"] = {"probe": True}
            elif index == 3:
                message["params"]["arguments"] = {"probe": True}
            elif index == 4:
                request_headers["Mcp-Session-Id"] = "private-probe"
            else:
                message["method"] = "probe"
            started = time.monotonic()
            try:
                response = await client.post(server.url, headers=request_headers, json=message)
                assert response.status_code != 200
                outcome = {"http_status": response.status_code}
            except httpx.HTTPError as error:
                outcome = {"transport_error": type(error).__name__}
            return dict(marker=index, request_id=value["request_id"], started_at=started, **outcome)
        attempts = await asyncio.gather(*(attempt(index) for index in range(6)))
    restoration = await restored(server, generation+1, fault_at=lease_until)
    ids = {row["request_id"] for row in attempts}
    assert not [row for row in server.events() if row.get("request_id") in ids and row.get("event") == "provider_entered"]
    return dict(private_probe=probes[0], absolute_lease_until=lease_until, public_attempts=attempts,
                unexpected_handlers=0, restoration=restoration,
                local_capability="Object identity inserted only by private HTTPX ASGI transport; no public selector.")
