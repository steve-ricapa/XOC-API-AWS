import asyncio
import io
import json
import sys
import tempfile
from unittest.mock import patch
import anyio
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client import stdio
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from xoc_mcp_lab.isolation import LAB_ROOT, clean_environment
from xoc_mcp_lab.transport import BoundedLines, BoundedWriter, INPUT_LIMIT, OUTPUT_LIMIT, wire_bytes
from xoc_mcp_lab.query_worker import BoundedReceive
from test_worker_lifecycle import observe_processes


class Sink:
    def __init__(self): self.value = ""
    async def write(self, value): self.value += value
    async def flush(self): pass


def response(value):
    return types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc="2.0", id=7, result={"value": value}))


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_complete_output_exact_byte_boundary(delta):
    overhead = len(wire_bytes(response("")))
    serialized = wire_bytes(response("x" * (OUTPUT_LIMIT - overhead + delta)))
    assert len(serialized) == OUTPUT_LIMIT + delta
    sink = Sink()
    asyncio.run(BoundedWriter(sink).write(serialized.decode()))
    if delta <= 0:
        assert sink.value.encode() == serialized
    else:
        out = types.JSONRPCMessage.model_validate_json(sink.value).root
        assert out.error.code == -32001 and out.id == 7
        assert len(sink.value.encode()) < 256 and "x" * 100 not in sink.value


@pytest.mark.parametrize("text", ['"\\\n\t' * 10000, "🚀" * 40000, "á" * 40000],
                         ids=["escaping", "emoji", "accent"])
def test_unicode_and_json_escaping_count_utf8_not_characters(text):
    data = wire_bytes(response(text))
    sink = Sink()
    asyncio.run(BoundedWriter(sink).write(data.decode()))
    assert sink.value.encode() == data
    assert types.JSONRPCMessage.model_validate_json(sink.value).root.result["value"] == text


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_input_newline_included_in_boundary(delta):
    value = b"x" * (INPUT_LIMIT - 1 + delta) + b"\n"
    async def scenario(): return await BoundedLines(io.BytesIO(value)).__anext__()
    if delta <= 0: assert asyncio.run(scenario()).encode() == value
    else:
        with pytest.raises(RuntimeError): asyncio.run(scenario())


@pytest.mark.parametrize("raw", [b"incomplete", b"\xff\n"])
def test_incomplete_or_invalid_utf8_not_repaired(raw):
    async def scenario(): return await BoundedLines(io.BytesIO(raw)).__anext__()
    with pytest.raises((RuntimeError, UnicodeDecodeError)): asyncio.run(scenario())


def test_client_checks_whole_message_limit():
    async def scenario():
        send, read = anyio.create_memory_object_stream(1)
        async with send, read:
            await send.send(SessionMessage(response("x" * OUTPUT_LIMIT)))
            with pytest.raises(ValueError): await BoundedReceive(read).receive()
    asyncio.run(scenario())


# Test-only launcher. No public fixture selector or output-injection option was
# added to the actual worker/server. Original providers/Gateway used for large data.
CHILD = '''
import asyncio, sys
from xoc_mcp_lab.isolation import Isolation, configure_logs
with Isolation():
    configure_logs()
    sys.path.insert(0, sys.argv[1])
    from xoc_mcp_lab.wiring import Wiring
    with Wiring() as w:
        from xoc_mcp_lab.adapter import Adapter
        from xoc_mcp_lab.transport import serve
        from mcp import types
        a = Adapter("admin_tenant_a", w)
        mode = sys.argv[2]
        if mode == "overflow":
            a.call = lambda *args: types.CallToolResult(content=[types.TextContent(type="text", text="x"*270000)])
        else:
            seed = w.rows["LAB_TICKETS"][0]
            fields = ("ticket_id", "id", "subject", "status", "severity")
            rows = []
            for i in range(35 if mode == "ascii" else 4):
                row = dict(seed, **{key:("x" if mode=="ascii" else chr(0x1F680))*240 for key in fields})
                row["sk"] = "TICKET#size-%03d" % i
                rows.append(row)
            w.rows["LAB_TICKETS"] = rows
        asyncio.run(serve(a))
'''


@pytest.mark.parametrize("mode", ["ascii", "unicode", "overflow"])
def test_official_sdk_stdio_receives_large_result_without_truncation(backend, mode):
    processes = []
    received = []
    params = StdioServerParameters(command=sys.executable,
        args=["-B", "-c", CHILD, str(backend), mode], env=clean_environment(), cwd=str(LAB_ROOT))
    class ObservedRead(BoundedReceive):
        async def receive(self):
            message = await super().receive()
            received.append(len(wire_bytes(message.message)))
            return message
    async def scenario(log):
        async with stdio.stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(ObservedRead(read), write) as session:
                await session.initialize()
                if mode == "overflow":
                    with pytest.raises(McpError) as error:
                        await session.call_tool("xoc.tickets.list", {})
                    assert error.value.error.code == -32001
                else:
                    result = await session.call_tool("xoc.tickets.list", {})
                    assert not result.isError
                    assert json.loads(result.content[0].text) == result.structuredContent
                    assert result.structuredContent["data"]["count"] == (35 if mode == "ascii" else 4)
                    assert "LAB_CANARY" not in result.content[0].text
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        with patch.object(stdio, "_create_platform_compatible_process", observe_processes(processes)):
            asyncio.run(scenario(log))
        log.seek(0)
        assert "x" * 300 not in log.read()
    if mode != "overflow": assert INPUT_LIMIT < max(received) <= OUTPUT_LIMIT
    else: assert max(received) < INPUT_LIMIT
    assert len(processes) == 1 and processes[0].returncode == 0
