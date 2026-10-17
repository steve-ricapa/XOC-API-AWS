"""LAB_ONLY additive L6 server; unchanged L4 boundary, dispatch and gateway."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import socket
import threading
import time

from xoc_mcp_lab_l4.boundary import BoundaryError
from xoc_mcp_lab_l4.scope import FaultPlan, optional_scope
from xoc_mcp_lab_l4.server import (EventFile, L4Application, _bounded_error_response,
                                    _output_path, mixed_fault_factory)

from .control import ControlChannel, ControlProtocolError
from .pool import BoundedObserver, BoundedPool, PrivateProbeCapability, _PRIVATE_SCOPE


def verify_local_clocks():
    """L4 provider perf_counter stamps must share our monotonic time domain."""
    before = time.monotonic()
    counter = time.perf_counter()
    after = time.monotonic()
    monotonic_info = time.get_clock_info("monotonic")
    counter_info = time.get_clock_info("perf_counter")
    if (not monotonic_info.monotonic or not counter_info.monotonic
            or monotonic_info.implementation != counter_info.implementation
            or not before <= counter <= after or after-before > .005):
        raise RuntimeError("LAB_MONOTONIC_DOMAIN_UNVERIFIED")
    return dict(before=before, counter=counter, after=after, maximum_bracket_seconds=after-before)


class L6Application(L4Application):
    def __init__(self, port, *, generation=1, fault_profile="none", observer=None):
        self.generation = generation
        self.fault_profile = fault_profile
        self.probe_capability = PrivateProbeCapability()
        self.probe_attempted = self.probe_ok = False
        self.probe_request_id = None
        self._owns_diagnostics = not isinstance(observer, BoundedObserver)
        self.diagnostics = observer if isinstance(observer, BoundedObserver) else BoundedObserver(observer)
        mixed = mixed_fault_factory()

        def factory(verified):
            if _PRIVATE_SCOPE.get() is self.probe_capability:
                if fault_profile == "probe_timeout":
                    return FaultPlan(first_read=lambda _: time.sleep(4.0))
                return None
            if fault_profile != "mixed":
                return None
            if verified.request_id.startswith("lab-fault-long"):
                return FaultPlan(first_read=lambda _: time.sleep(4.0))
            return mixed(verified)

        super().__init__(port, fault_factory=factory, observer=self._observe)
        self.pool.shutdown(wait=False)
        self.pool = BoundedPool(generation=generation, capability=self.probe_capability,
                                observer=self.diagnostics)

    def _observe(self, event):
        current = optional_scope()
        identity = self.pool.diagnostic_identity(current) if current is not None else {}
        self.diagnostics(dict(event | identity, generation=self.generation))

    def snapshot(self):
        return dict(super().snapshot(), diagnostics=self.diagnostics.snapshot())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await super().__call__(scope, receive, send)
        private = scope.get("xoc_l6_private_capability") is self.probe_capability
        if not self.pool.gate.permits(private):
            self._observe(dict(event="gate_rejected", monotonic=time.monotonic(),
                               safe_reason=self.pool.gate.safe_reason))
            return await _bounded_error_response(send, BoundaryError("LAB_ADMISSION_CLOSED", 503, -32003))
        token = _PRIVATE_SCOPE.set(self.probe_capability if private else None)

        async def guarded_send(message):
            current = optional_scope()
            if (not private and self.fault_profile == "mixed" and current is not None
                    and current.verified.request_id.startswith("lab-fault-response-lost")
                    and message["type"] == "http.response.body"
                    and current.safe_summary()["handler_calls"] == 1):
                self._observe(dict(event="delivery_lost", monotonic=time.monotonic(),
                                   **current.safe_summary()))
                raise ConnectionResetError("LAB_INJECTED_DELIVERY_LOSS")
            await send(message)

        try:
            return await super().__call__(scope, receive, guarded_send)
        finally:
            _PRIVATE_SCOPE.reset(token)

    def close_diagnostics(self):
        if self._owns_diagnostics:
            self.diagnostics.close()


def create_app(port, fault_factory=None, observer=None, *, generation=1, fault_profile="none"):
    if fault_factory is not None:
        raise ValueError("LAB_USE_TRUSTED_STARTUP_PROFILE")
    return L6Application(port, generation=generation, fault_profile=fault_profile, observer=observer)


async def control_loop(app, channel, server):
    """Both command handling and heartbeat run on the ACTUAL server event loop."""
    next_heartbeat = 0.0
    noack = False
    probe_task = None
    fault_reported = False

    async def probe():
        from .probe import run_private_probe
        if app.fault_profile == "startup_hold":
            # Trusted C07 scheduling fixture only. Not part of normal startup,
            # provider fault injection, or any measured healthy population.
            await asyncio.sleep(.2)
        result = await run_private_probe(app, negative="denied" if app.fault_profile == "probe_denied" else None)
        app._observe(dict(event="private_probe_finished", **result))
        channel.send("probe_result", **result)

    try:
        while not server.should_exit:
            try:
                messages = channel.receive()
            except ControlProtocolError:
                app.pool.gate.close("control_invalid")
                messages = []
            for message in messages:
                kind = message["kind"]
                if kind == "lease":
                    app.pool.gate.renew(message["generation"], message["seq"], message["lease_until"])
                elif kind == "snapshot_request":
                    # Technical freshness request only. The ordinary heartbeat
                    # below samples the pool under its lock on this real loop;
                    # it does not run a probe, admit work or open the gate.
                    next_heartbeat = 0.0
                elif kind == "close":
                    if noack:
                        continue
                    app.pool.gate.close(message.get("reason", "control_close"))
                    channel.send("ack", command="close", command_seq=message["seq"], gate_state="CLOSED",
                                 occupancy=app.pool.snapshot()["active"])
                    app._observe(dict(event="gate_closed", monotonic=time.monotonic(), command_seq=message["seq"]))
                elif kind == "probe":
                    if probe_task is None:
                        probe_task = asyncio.create_task(probe(), name="xoc-l6-private-probe")
                    else:
                        channel.send("probe_result", ok=False, occupancy=app.pool.snapshot()["active"],
                                     safe_reason="probe_already_used")
                elif kind == "open":
                    snapshot = app.pool.snapshot()
                    ready = app.probe_ok and snapshot["active"] == 0 and snapshot["reconciled"]
                    if app.pool.gate.open(message["generation"], message["seq"], message["lease_until"], ready=ready):
                        channel.send("ack", command="open", command_seq=message["seq"], gate_state="OPEN", occupancy=0)
                        app._observe(dict(event="gate_opened", monotonic=time.monotonic()))
                    else:
                        channel.send("fault", safe_reason="open_preconditions_failed")
                elif kind == "stop":
                    app.pool.gate.close("supervisor_stop")
                    channel.send("ack", command="stop", command_seq=message["seq"], gate_state="CLOSED")
                    server.should_exit = True
                elif kind == "test":
                    action = message["action"]
                    duration = message.get("duration", 10.0)
                    if action == "noack":
                        noack = True
                    if action == "observer_block":
                        app.diagnostics.block()
                    if action == "observer_unblock":
                        app.diagnostics.unblock()
                    channel.send("ack", command="test", command_seq=message["seq"],
                                 gate_state=app.pool.gate.state, action=action)
                    # Flush ACK before deliberately blocking/crashing this event loop.
                    channel.flush()
                    app._observe(dict(event="test_injected", action=action, monotonic=time.monotonic()))
                    if action == "loop_block":
                        time.sleep(duration)
                    elif action == "crash":
                        os._exit(86)
                    elif action == "close_control":
                        channel.close()
                    elif action == "stale_snapshot" and app.generation > 1:
                        snapshot = app.pool.snapshot()
                        channel.send_test_message(dict(kind="heartbeat", generation=app.generation-1,
                            seq=channel.last_sent_seq+10000, sent_at=time.monotonic(),
                            heartbeat=time.monotonic(), gate_state="CLOSED", occupancy=0, jobs=[]))
            now = time.monotonic()
            if now >= next_heartbeat:
                snapshot = app.pool.snapshot()
                channel.send("heartbeat", heartbeat=now, gate_state=snapshot["gate_state"],
                    occupancy=snapshot["active"], jobs=snapshot["jobs"], abandoned=snapshot["abandoned"],
                    lease_until=snapshot["lease_until"], safe_reason=snapshot["safe_reason"])
                app._observe(dict(event="control_snapshot", monotonic=now, **app.snapshot()))
                if not snapshot["reconciled"] and not fault_reported:
                    channel.send("fault", safe_reason="accounting_mismatch")
                    fault_reported = True
                next_heartbeat = now + 0.25
            await asyncio.sleep(0.01)
    finally:
        if probe_task is not None and not probe_task.done():
            probe_task.cancel()
            try:
                await probe_task
            except asyncio.CancelledError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="LAB_ONLY L6 supervised loopback server")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--control-fd", required=True, type=int)
    parser.add_argument("--events-file", required=True)
    parser.add_argument("--fault-profile", default="none", choices=("none", "mixed", "probe_denied",
                        "probe_timeout", "integrity_fail", "startup_fail", "listener_fail", "startup_hold"))
    args = parser.parse_args(argv)
    channel = observer = app = None
    try:
        channel = ControlChannel(socket.socket(fileno=args.control_fd), args.generation)
        if args.fault_profile == "startup_fail":
            raise RuntimeError("LAB_INJECTED_STARTUP_FAILURE")
        from xoc_mcp_lab.isolation import configure_logs
        from xoc_mcp_lab_l4.wiring import PersistentWiring, ScopedIsolation
        from .integrity import require_l6_installed
        with ScopedIsolation():
            configure_logs()
            clocks = verify_local_clocks()
            require_l6_installed()
            if args.fault_profile == "integrity_fail":
                raise RuntimeError("LAB_INJECTED_INTEGRITY_FAILURE")
            with PersistentWiring():
                from xoc_mcp_lab import adapter as _canonical_adapter
                observer = BoundedObserver(EventFile(_output_path(args.events_file)))
                observer(dict(event="clock_domain_checked", generation=args.generation, **clocks))
                app = create_app(args.port, generation=args.generation, fault_profile=args.fault_profile, observer=observer)
                import uvicorn

                class ControlledServer(uvicorn.Server):
                    async def startup(self, sockets=None):
                        if args.fault_profile == "listener_fail":
                            raise RuntimeError("LAB_INJECTED_LISTENER_FAILURE")
                        await super().startup(sockets=sockets)
                        if self.started:
                            channel.send("hello", monotonic=time.monotonic(), pid=os.getpid(),
                                         integrity_ok=True, listener_bound=True, fixtures_ok=True)
                            self.control_task = asyncio.create_task(control_loop(app, channel, self))

                    async def shutdown(self, sockets=None):
                        app.pool.gate.close("server_shutdown")
                        task = getattr(self, "control_task", None)
                        if task is not None:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                        await super().shutdown(sockets=sockets)

                config = uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1,
                    loop="asyncio", http="h11", lifespan="on", access_log=False,
                    log_config=None, log_level="warning", proxy_headers=False,
                    timeout_keep_alive=2, limit_concurrency=32, h11_max_incomplete_event_size=65536)
                ControlledServer(config).run()
                if app.pool.snapshot()["active"]:
                    app._observe(dict(event="server_forced_exit", reason="active_sync_workers", **app.snapshot()))
                    observer.close()
                    os._exit(124)
        return 0
    except (Exception, KeyboardInterrupt):
        if channel is not None:
            channel.send("fault", safe_reason="startup_failed")
            channel.flush()
        return 2
    finally:
        if observer is not None:
            observer.close()
        if channel is not None:
            channel.close()


if __name__ == "__main__":
    raise SystemExit(main())
