"""Actual installed server + official SDK; no fake HTTP substitute."""
import asyncio
import base64
import copy
import json
import time

import httpx
import pytest

from xoc_mcp_lab_l4.client import QueryClient, headers_for, local_url
from xoc_mcp_lab_l4.harness import ServerProcess, make_envelope, query, check_result, TOOLS


@pytest.fixture(autouse=True)
def installed_integration_only(request):
    from xoc_mcp_lab.installed import is_installed
    if not any(part in request.node.name for part in ('loopback_only','approved_endpoint')) and not is_installed():
        pytest.skip('HTTP integration is validated with installed artifacts in Linux, never backend checkout')


@pytest.mark.parametrize('url', ['https://127.0.0.1:99/mcp','http://localhost:99/mcp',
    'http://192.168.1.1/mcp','http://127.0.0.1:99/mcp?a=b','http://127.0.0.1:99/other',
    'http://user:pass@127.0.0.1:99/mcp','http://127.0.0.1:99/mcp#fragment'])
def test_l4_client_loopback_only(url):
    with pytest.raises(ValueError):
        local_url(url)


def test_l4_transport_locks_approved_endpoint_without_network():
    from xoc_mcp_lab_l4.client import LoopbackTransport
    async def run():
        transport=LoopbackTransport('http://127.0.0.1:12345/mcp')
        try:
            with pytest.raises(ValueError,match='LAB_APPROVED_ENDPOINT_REQUIRED'):
                await transport.handle_async_request(httpx.Request('POST','http://127.0.0.1:12346/mcp'))
        finally:
            await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('status', [302, 307, 308])
def test_l4_redirects_to_approved_endpoint_never_replay_signed_header(status):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from xoc_mcp_lab_l4.client import LoopbackTransport

    class RedirectBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'LAB_REDIRECT_FIXTURE'

        async def aclose(self):
            self.closed = True

    async def run():
        endpoint = 'http://127.0.0.1:12345/mcp'
        calls, bodies = [], []

        async def redirect(request):
            assert request.headers.get('X-XOC-Lab-Envelope') is not None
            calls.append((request.method, str(request.url)))
            body = RedirectBody()
            bodies.append(body)
            return httpx.Response(status, headers={'Location': endpoint}, stream=body)

        transport = LoopbackTransport(endpoint)
        await transport.inner.aclose()
        transport.inner = httpx.MockTransport(redirect)
        async with httpx.AsyncClient(transport=transport, headers=headers_for(make_envelope()),
                                    trust_env=False, follow_redirects=False) as client:
            with pytest.raises(Exception) as caught:
                async with streamable_http_client(endpoint, http_client=client,
                                                  terminate_on_close=False) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(session.initialize(), timeout=1)
            pending, leaves = [caught.value], []
            while pending:
                error = pending.pop()
                children = getattr(error, 'exceptions', ())
                if children:
                    pending.extend(children)
                else:
                    leaves.append(error)
            assert any(isinstance(error, ValueError) and str(error) == 'LAB_REDIRECT_BLOCKED'
                       for error in leaves)
            assert all(endpoint not in str(error) and 'eyJ' not in str(error) for error in leaves)
        assert calls == [('POST', endpoint)]
        assert len(bodies) == 1 and bodies[0].closed
    asyncio.run(run())


def test_l4_official_sdk_smoke_and_single_use():
    from xoc_mcp_lab_l4.harness import smoke
    result = asyncio.run(smoke())
    assert result['status']=='passed'
    assert smoke.last_cleanup['process_absent']


def call_message(name=TOOLS[0], args=None, rid=7):
    return {'jsonrpc':'2.0','id':rid,'method':'tools/call',
        'params': {'name':name,'arguments':{} if args is None else args}}


def test_l4_http_identity_denials_and_binding():
    async def run():
        server = ServerProcess()
        try:
            await server.wait_ready()
            value = make_envelope()
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                async def post(env, message=None, extra=None):
                    headers = headers_for(env) if env else {}
                    headers.update({'Accept':'application/json, text/event-stream'})
                    headers.update(extra or {})
                    return await client.post(server.url, headers=headers, json=message or call_message())
                assert (await post(value)).status_code==200
                assert (await post(None)).status_code==401
                mutations = [dict(aud='wrong'),dict(purpose='wrong'),dict(source='wrong'),
                    dict(exp=int(time.time())-1, deadline=int(time.time())-2),
                    dict(actor_user_id=42),dict(tenant_id=1),dict(delegation_active='true'),
                    dict(actor_role=None),dict(tool_hash='0'*64)]
                for claims in mutations:
                    assert (await post(make_envelope(patch=claims))).status_code==401
                from xoc_mcp_lab_harness.signing import envelope
                assert (await post(envelope(scalar=2))).status_code==401
                assert (await post(envelope(headers_patch={'kid':'unknown'}))).status_code==401
                assert (await post(envelope(headers_patch={'alg':'ES384'}))).status_code==401
                changed = copy.deepcopy(value)
                changed['request_id']='lab-tampered'
                assert (await post(changed)).status_code==401
                assert (await post(value, call_message(TOOLS[3]))).status_code==403
                assert (await post(value, call_message(args={'limit':1}))).status_code==403
                # ClientInfo and arbitrary decorative headers are never authority.
                for key in ('tenant_id','tenantId','user_id','userId','role','effective_tenant_id'):
                    injected = make_envelope(args={key:91002})
                    response = await post(injected, call_message(args={key:91002}))
                    assert response.status_code==200
                    assert response.json()['result']['structuredContent']['status']=='denied'
                meta = call_message()
                meta['params']['_meta']={'tenant_id':91002,'role':'SUPERADMIN'}
                assert (await post(value, meta)).status_code==400
                response = await post(value, extra={'X-Tenant-Id':'91002','Mcp-Session-Id':'invented'})
                assert response.status_code==200
                assert 'lab-ticket-b' not in response.text
                assert 'mcp-session-id' not in response.headers
                for method in ('GET','DELETE','OPTIONS'):
                    response = await client.request(method, server.url, headers=headers_for(value))
                    assert response.status_code==405
                    assert 'access-control-allow-origin' not in response.headers
                assert (await post(value, extra={'Origin':'https://evil.invalid'})).status_code==403
                assert (await post(value, extra={'Host':'localhost:'+str(server.port)})).status_code==421
                assert (await post(value, extra={'MCP-Protocol-Version':'2024-11-05'})).status_code==400
        finally:
            assert server.close()['process_absent']
    asyncio.run(run())


def test_l4_interleaving_concurrency_same_ids_no_authority_reuse():
    async def run():
        server = ServerProcess('mixed')
        try:
            await server.wait_ready()
            for profile in ('user_a1','admin_tenant_b','user_a2','user_a1'):
                result = await query(server, profile, TOOLS[3])
                check_result(result, profile, TOOLS[3])
            async with httpx.AsyncClient(trust_env=False) as client:
                async def post(profile):
                    # Equal signed request_id AND equal JSON-RPC id, different identities.
                    value = make_envelope(profile, TOOLS[3], patch={'request_id':'lab-same-rid'})
                    headers = headers_for(value)
                    headers['Accept']='application/json, text/event-stream'
                    response = await client.post(server.url, headers=headers, json=call_message(TOOLS[3],rid=7))
                    assert response.status_code==200
                    return response.json()['result']['structuredContent']['data']['count']
                assert await asyncio.gather(post('user_a1'),post('admin_tenant_b'),post('user_a2'),post('user_a1'))==[2,3,5,2]
            # Real provider barrier proves A and B have entered together, not serialized happy paths.
            a,b = await asyncio.gather(query(server,'user_a1',TOOLS[3],prefix='fault-barrier'),
                query(server,'admin_tenant_b',TOOLS[3],prefix='fault-barrier'))
            check_result(a,'user_a1',TOOLS[3])
            check_result(b,'admin_tenant_b',TOOLS[3])
            error = await query(server,prefix='fault-error')
            assert error['status']!='allowed'
            check_result(await query(server,'admin_tenant_b',TOOLS[3]),'admin_tenant_b',TOOLS[3])
        finally:
            assert server.close()['process_absent']
    asyncio.run(run())


def test_l4_actual_blocked_provider_B_healthy_and_slot_retained():
    async def run():
        server = ServerProcess('mixed')
        try:
            await server.wait_ready()
            value = make_envelope(prefix='fault-block')
            task = asyncio.create_task(QueryClient(server.url).run(value, timeout=.7))
            await server.wait_event(value['request_id'])
            b = await query(server,'admin_tenant_b',TOOLS[3])
            check_result(b,'admin_tenant_b',TOOLS[3])
            assert (await task)['status']=='timeout'
            # A disconnect did not kill A. Three more real blocked handlers fill slots.
            values = [make_envelope(prefix='fault-block') for _ in range(3)]
            tasks = [asyncio.create_task(QueryClient(server.url).run(v, timeout=.7)) for v in values]
            for other in values:
                await server.wait_event(other['request_id'])
            fifth = await query(server)
            assert fifth['code']=='lab_capacity_busy'
            assert fifth['total_seconds']<3.5
            await asyncio.gather(*tasks)
        finally:
            cleanup = server.close()
            assert cleanup['process_absent']
            assert cleanup['forced_kill'] or cleanup['returncode']==124
    asyncio.run(run())


def test_l4_real_sdk_large_result_and_projection():
    async def run():
        server = ServerProcess('mixed')
        try:
            await server.wait_ready()
            result = await query(server,prefix='large')
            assert result['status']=='allowed'
            events = server.events()
            assert any((e.get('output_bytes') or 0)>65536 for e in events)
            assert len(json.dumps(result['result']))>30000
            assert 'LAB_CANARY_NEVER_PUBLIC' not in json.dumps(result)
        finally:
            assert server.close()['process_absent']
    asyncio.run(run())
