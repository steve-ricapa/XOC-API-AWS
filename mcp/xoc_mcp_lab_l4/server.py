"""LAB_ONLY persistent loopback server. No production issuer, signer or configuration."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import threading
import time
import weakref

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from starlette.applications import Starlette
from starlette.requests import Request

from .boundary import (BoundaryError, BoundedResponse, PROTOCOL_VERSION, INPUT_LIMIT,
                       checked_headers, checked_message, error_response, read_body, verified_header)
from .pool import BoundedPool, BusyError
from .scope import FaultPlan, RequestScope, bind_scope, current_scope, optional_scope

CALL_TIMEOUT_SECONDS = 3.0
REQUEST_LIFETIME_SECONDS = 5.0
ERROR_SEND_TIMEOUT_SECONDS = 0.05


def _observe(observer, event):
    if observer is not None:
        try:
            observer(event)
        except Exception:
            # No diagnostic failure may change authority or a business result.
            pass


async def _bounded_error_response(send, error, remaining_max=ERROR_SEND_TIMEOUT_SECONDS):
    timeout = min(ERROR_SEND_TIMEOUT_SECONDS, remaining_max)
    if timeout <= 0:
        return
    try:
        with anyio.move_on_after(timeout):
            await error_response(send, error)
    except Exception:
        # A disconnected/non-reading peer cannot retain a request to receive its error.
        pass


class L4Application:
    def __init__(self, port, fault_factory=None, observer=None):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("LAB_INVALID_PORT")
        from .wiring import require_persistent_wiring
        require_persistent_wiring()
        self.port, self.fault_factory, self.observer = port, fault_factory, observer
        self.pending_requests = self.max_pending_requests = 0
        # Weak observation only: neither lookup nor key participates in authority.
        self._live_scopes = weakref.WeakValueDictionary()
        self.pool = BoundedPool(workers=4, observer=observer)
        self.sdk = Server("xoc-mcp-l4-lab", version="0.4.0",
            instructions="LAB_ONLY signed fictional context per HTTP request. Not production authentication.")
        self.manager = StreamableHTTPSessionManager(self.sdk, stateless=True, json_response=True,
            max_request_body_size=INPUT_LIMIT,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                allowed_hosts=[f"127.0.0.1:{port}"], allowed_origins=[f"http://127.0.0.1:{port}"]))

        def bound_request():
            request = self.sdk.request_context.request
            scope = current_scope()
            if not isinstance(request, Request) or request.scope.get("xoc_l4_scope") is not scope:
                raise McpError(types.ErrorData(code=-32603, message="LAB_INVALID_REQUEST_CONTEXT"))
            scope.verified.require_valid()
            return scope

        async def execute(scope, function):
            timeout = min(CALL_TIMEOUT_SECONDS, scope.verified.deadline - time.time())
            try:
                return await self.pool.run(scope, function, timeout_seconds=timeout)
            except BusyError:
                raise McpError(types.ErrorData(code=-32003, message="LAB_CAPACITY_BUSY")) from None
            except TimeoutError:
                raise McpError(types.ErrorData(code=-32002, message="LAB_REQUEST_TIMEOUT")) from None
            except Exception:
                raise McpError(types.ErrorData(code=-32603, message="LAB_INTERNAL_ERROR")) from None

        async def list_tools(request):
            scope = bound_request()
            tools = await execute(scope, lambda: scope.adapter.list_tools())
            return types.ServerResult(types.ListToolsResult(tools=tools))

        async def call_tool(request):
            scope = bound_request()
            params = request.params
            arguments = params.arguments if "arguments" in params.model_fields_set else {}
            if params.model_extra or params.task is not None or params.meta is not None:
                raise McpError(types.ErrorData(code=-32602, message="LAB_REQUEST_MISMATCH"))
            if not scope.verified.matches(params.name, arguments):
                raise McpError(types.ErrorData(code=-32602, message="LAB_REQUEST_MISMATCH"))
            result = await execute(scope, lambda: scope.adapter.call(params.name, arguments))
            return types.ServerResult(result)

        # Typed public dispatch matches canonical transport and avoids decorator coercion/cache.
        self.sdk.request_handlers[types.ListToolsRequest] = list_tools
        self.sdk.request_handlers[types.CallToolRequest] = call_tool

        @asynccontextmanager
        async def lifespan(_):
            async with self.manager.run():
                try:
                    yield
                finally:
                    self.pool.shutdown(wait=False)
        self.lifecycle = Starlette(lifespan=lifespan)

    def snapshot(self):
        try:
            tasks = len(asyncio.all_tasks())
        except RuntimeError:
            tasks = None
        return dict(pool=self.pool.snapshot(), threads=threading.active_count(), tasks=tasks,
                    pending_requests=self.pending_requests, max_pending_requests=self.max_pending_requests,
                    live_request_scopes=len(self._live_scopes))

    async def __call__(self, asgi_scope, receive, send):
        if asgi_scope["type"] == "lifespan":
            return await self.lifecycle(asgi_scope, receive, send)
        if asgi_scope["type"] != "http":
            return
        self.pending_requests += 1
        self.max_pending_requests = max(self.max_pending_requests, self.pending_requests)
        started = time.perf_counter_ns()
        request_scope = None
        verified = None
        message = None
        response = None
        outcome = "rejected"
        input_bytes = 0
        verify_bind_ns = body_receive_ns = dispatch_ns = context_reset_ns = 0

        def remaining_budget():
            remaining = REQUEST_LIFETIME_SECONDS - (time.perf_counter_ns() - started) / 1e9
            if verified is not None:
                remaining = min(remaining, verified.deadline - time.time())
            return remaining

        try:
            headers = checked_headers(asgi_scope, self.port)
            verified = verified_header(headers)
            verify_bind_ns = time.perf_counter_ns() - started
            body_start = time.perf_counter_ns()
            body = await read_body(receive, headers, verified.deadline)
            body_receive_ns = time.perf_counter_ns() - body_start
            input_bytes = len(body)
            binding_start = time.perf_counter_ns()
            message = checked_message(body, headers, verified)
            plan = self.fault_factory(verified) if self.fault_factory is not None else None

            def first_read(scope):
                _observe(self.observer, dict(event="handler_entered", request_id=scope.verified.request_id,
                    handler_calls=scope.adapter.handler_calls, observed_at="first_provider_read",
                    monotonic_ns=time.perf_counter_ns()))
                _observe(self.observer, dict(event="provider_entered", request_id=scope.verified.request_id,
                    handler_calls=scope.adapter.handler_calls,
                    monotonic_ns=time.perf_counter_ns(), pool=self.pool.snapshot()))
                if plan is not None and plan.first_read is not None:
                    plan.first_read(scope)

            request_scope = RequestScope(verified, faults=FaultPlan(first_read=first_read))
            self._live_scopes[id(request_scope)] = request_scope
            verify_bind_ns += time.perf_counter_ns() - binding_start
            forwarded_scope = dict(asgi_scope, xoc_l4_scope=request_scope)
            sent_body = False

            async def replay_receive():
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            response = BoundedResponse(send, request_id=message.get("id"))
            dispatch_start = time.perf_counter_ns()
            reset_start = None
            try:
                with bind_scope(request_scope):
                    try:
                        # Reserve error delivery INSIDE the same total budget;
                        # never add an explicit grace period beyond five seconds.
                        remaining = remaining_budget() - ERROR_SEND_TIMEOUT_SECONDS
                        if remaining <= 0:
                            raise TimeoutError()
                        with anyio.fail_after(remaining):
                            await self.manager.handle_request(forwarded_scope, replay_receive, response)
                    finally:
                        reset_start = time.perf_counter_ns()
            finally:
                dispatch_ns = time.perf_counter_ns() - dispatch_start
                if reset_start is not None:
                    context_reset_ns = time.perf_counter_ns() - reset_start
            outcome = "served"
        except BoundaryError as error:
            await _bounded_error_response(send, error, remaining_budget())
        except TimeoutError:
            outcome = "deadline"
            if response is None or not response.sent:
                await _bounded_error_response(send, BoundaryError("LAB_REQUEST_TIMEOUT", 408, -32002),
                                              remaining_budget())
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            if response is None or not response.sent:
                await _bounded_error_response(send, BoundaryError("LAB_INTERNAL_ERROR", 500, -32603),
                                              remaining_budget())
            outcome = "internal_error"
        finally:
            self.pending_requests -= 1
            event = dict(event="request_finished", outcome=outcome, context_cleared=optional_scope() is None,
                elapsed_ns=time.perf_counter_ns() - started, input_bytes=input_bytes,
                output_bytes=response.wire_bytes if response is not None else None,
                verify_bind_ns=verify_bind_ns, body_receive_ns=body_receive_ns,
                dispatch_ns=dispatch_ns, context_reset_ns=context_reset_ns,
                response_guard_ns=response.guard_ns if response is not None else 0,
                sdk_serialization_in_dispatch=True, **self.snapshot())
            if request_scope is not None:
                event.update(request_scope.safe_summary())
                event["method"] = message["method"]
            _observe(self.observer, event)


def create_app(port, fault_factory=None, observer=None):
    """Create once, after PersistentWiring startup. Caller owns process isolation."""
    return L4Application(port, fault_factory=fault_factory, observer=observer)


def mixed_fault_factory():
    """Trusted startup-only benchmark profile; selection is signed fixture request ID."""
    barrier = threading.Barrier(2, timeout=4.0)
    blocked = threading.Event()

    def factory(verified):
        rid = verified.request_id

        def first_read(scope):
            if rid.startswith("lab-fault-error"):
                raise RuntimeError("LAB_CANARY_NEVER_PUBLIC fixture provider error")
            if rid.startswith("lab-fault-slow"):
                time.sleep(2.0)
            if rid.startswith("lab-fault-block"):
                blocked.wait()  # Deliberately noncooperative: only trusted separate process test.
            if rid.startswith("lab-fault-barrier"):
                barrier.wait(timeout=4.0)
            if rid.startswith("lab-large-"):
                seed = scope.wiring.rows["LAB_TICKETS"][0]
                fields = ("ticket_id", "id", "subject", "status", "severity")
                scope.wiring.rows["LAB_TICKETS"] = [dict(seed, sk=f"TICKET#large-{i:03d}",
                    **{key: "x" * 240 for key in fields}) for i in range(35)]
        return FaultPlan(first_read=first_read)
    return factory


class EventFile:
    """Finite, flushed metadata-only observer; no retained scopes or raw messages."""
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._count = 0
        self._stream = path.open("x", encoding="utf-8", newline="\n")

    def __call__(self, event):
        with self._lock:
            if self._count >= 8192:
                return
            self._stream.write(json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n")
            self._stream.flush()
            self._count += 1

    def close(self):
        with self._lock:
            self._stream.close()


def _output_path(value):
    path = Path(value)
    if not path.is_absolute() or not path.parent.is_dir() or path.exists() or path.is_symlink():
        raise ValueError("LAB_NEW_ABSOLUTE_OUTPUT_PATH_REQUIRED")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="LAB_ONLY signed loopback Streamable HTTP")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--events-file", required=True)
    parser.add_argument("--fault-profile", choices=("none", "mixed"), default="none")
    args = parser.parse_args(argv)
    observer = None
    start = time.perf_counter_ns()
    try:
        ready_path, events_path = _output_path(args.ready_file), _output_path(args.events_file)
        if ready_path == events_path:
            raise ValueError("LAB_DISTINCT_OUTPUT_PATHS_REQUIRED")
        from xoc_mcp_lab.installed import require_installed as require_c1
        from xoc_mcp_lab.isolation import configure_logs
        from .integrity import require_l4_installed as require_l4
        from .wiring import PersistentWiring, ScopedIsolation
        with ScopedIsolation() as isolation:
            configure_logs()
            require_c1()
            require_l4()
            verified_ns = time.perf_counter_ns()
            with PersistentWiring():
                # Amortize canonical import/registry work once, with fake provider
                # boundaries already active, before accepting or announcing traffic.
                from xoc_mcp_lab import adapter as _canonical_adapter
                wired_ns = time.perf_counter_ns()
                observer = EventFile(events_path)
                app = create_app(args.port, mixed_fault_factory() if args.fault_profile == "mixed" else None, observer)
                import uvicorn

                class ReadyServer(uvicorn.Server):
                    async def startup(self, sockets=None):
                        await super().startup(sockets=sockets)
                        if self.started:
                            ready = dict(lab_only=True, pid=os.getpid(), protocol=PROTOCOL_VERSION,
                                sdk="1.30.0", url=f"http://127.0.0.1:{args.port}/mcp",
                                startup_ns=time.perf_counter_ns() - start,
                                verification_ns=verified_ns - start, wiring_ns=wired_ns - verified_ns,
                                process_network_attempts=isolation.network_attempts,
                                process_secret_file_attempts=isolation.secret_file_attempts,
                                **app.snapshot())
                            with ready_path.open("x", encoding="utf-8") as stream:
                                json.dump(ready, stream, ensure_ascii=True, separators=(",", ":"))

                config = uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1,
                    loop="asyncio", http="h11", lifespan="on", access_log=False,
                    log_config=None, log_level="warning", proxy_headers=False,
                    timeout_keep_alive=2, limit_concurrency=32,
                    h11_max_incomplete_event_size=65536)
                ReadyServer(config).run()
                if app.pool.snapshot()["active"]:
                    # A sync timeout never kills its thread. Do not restore real
                    # providers/network under unfinished work during shutdown.
                    _observe(observer, dict(event="server_forced_exit", reason="active_sync_workers",
                                            **app.snapshot()))
                    observer.close()
                    os._exit(124)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        import sys
        print("LAB_L4_SERVER_FAILED", file=sys.stderr)
        return 2
    finally:
        if observer is not None:
            observer.close()


if __name__ == "__main__":
    raise SystemExit(main())
