"""Shared SDK stdio transport. Whole-message UTF-8 bounds, including newline."""
import io
import os
import sys
import anyio
from mcp import types

INPUT_LIMIT = 64 * 1024
OUTPUT_LIMIT = 256 * 1024


def wire_bytes(message):
    return (message.model_dump_json(by_alias=True, exclude_none=True) + "\n").encode("utf-8")


class BoundedLines:
    def __init__(self, stream):
        self.stream = stream

    def __aiter__(self):
        return self

    async def __anext__(self):
        line = await anyio.to_thread.run_sync(self.stream.readline, INPUT_LIMIT + 1)
        if not line:
            raise StopAsyncIteration
        if len(line) > INPUT_LIMIT or not line.endswith(b"\n"):
            raise RuntimeError("LAB_INPUT_LIMIT_OR_INCOMPLETE_LINE")
        return line.decode("utf-8", errors="strict")


class BoundedWriter:
    """Receives the official SDK serialization, never cuts JSON or text."""
    def __init__(self, stream):
        self.stream = stream

    async def write(self, serialized):
        if len(serialized.encode("utf-8")) > OUTPUT_LIMIT:
            message = types.JSONRPCMessage.model_validate_json(serialized).root
            rid = getattr(message, "id", None)
            if type(rid) not in (int, str) or len(str(rid)) > 128:
                rid = None
            error = types.JSONRPCMessage(types.JSONRPCError(
                jsonrpc="2.0", id=rid, error=types.ErrorData(
                    code=-32001, message="LAB_OUTPUT_LIMIT")))
            serialized = wire_bytes(error).decode("utf-8")
        return await self.stream.write(serialized)

    async def flush(self):
        return await self.stream.flush()


async def serve(adapter):
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.shared.message import SessionMessage
    from mcp.shared.exceptions import McpError
    app = Server("xoc-mcp-lab",version="0.1.0",instructions="Fictional fixed context. No production authentication.")
    @app.list_tools()
    async def tools():
        return adapter.list_tools()
    # SDK decorator in 1.30.0 uses 'arguments or {}'. Keep the typed request to
    # distinguish omitted arguments; envelope guard below rejects explicit null.
    async def call(req):
        from xoc_mcp_lab.adapter import MISSING
        if req.params.model_extra or req.params.task is not None:
            raise McpError(types.ErrorData(code=-32602,message="Unsupported tool request parameters"))
        args = req.params.arguments if "arguments" in req.params.model_fields_set else MISSING
        return types.ServerResult(adapter.call(req.params.name,args))
    app.request_handlers[types.CallToolRequest] = call

    # Reserve protocol stdout, redirect incidental Python prints/import diagnostics.
    wire = anyio.wrap_file(io.TextIOWrapper(os.fdopen(os.dup(sys.stdout.fileno()),"wb"),
                                          encoding="utf-8", newline="\n"))
    sys.stdout = sys.stderr
    async with stdio_server(stdin=BoundedLines(sys.stdin.buffer),stdout=BoundedWriter(wire)) as (incoming,outgoing):
        sender, receiver = anyio.create_memory_object_stream(0)
        async def guard():
            async with sender:
                async for message in incoming:
                    if isinstance(message,Exception):
                        # No identifiable request ID: terminate this malformed stream safely.
                        raise RuntimeError("LAB_INVALID_PROTOCOL_INPUT")
                    root = message.message.root
                    if isinstance(root,types.JSONRPCRequest) and (
                            type(root.id) not in (int,str) or len(str(root.id)) > 128):
                        raise RuntimeError("LAB_INVALID_REQUEST_ID")
                    if isinstance(root,types.JSONRPCRequest) and root.method=="tools/call":
                        params = root.params
                        if isinstance(params,dict) and "arguments" in params and params["arguments"] is None:
                            await outgoing.send(SessionMessage(types.JSONRPCMessage(types.JSONRPCError(
                                jsonrpc="2.0",id=root.id,error=types.ErrorData(code=-32602,
                                message="Explicit null arguments are not accepted by the laboratory")))))
                            continue
                    await sender.send(message)
        async with anyio.create_task_group() as group:
            group.start_soon(guard)
            await app.run(receiver,outgoing,app.create_initialization_options())
            group.cancel_scope.cancel()
