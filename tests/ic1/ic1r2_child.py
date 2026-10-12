"""IC-1R.2 diagnostic entrypoint; delegates original main without changing exit."""
import os
import time
import json
import sys
import threading
import asyncio
import gc
import atexit


def emit(event, **fields):
    os.write(2, ("ic1r2_child " + json.dumps(dict(event=event, pid=os.getpid(),
        mono=time.monotonic(), **fields), separators=(",", ":")) + "\n").encode())


def snapshot(event, loop=None):
    frames = sys._current_frames()
    threads = []
    for thread in threading.enumerate():
        frame = frames.get(thread.ident)
        threads.append(dict(name=thread.name, daemon=thread.daemon,
            module=frame.f_globals.get("__name__") if frame else None,
            function=frame.f_code.co_name if frame else None))
    tasks = []
    if loop is not None and not loop.is_closed():
        for task in asyncio.all_tasks(loop):
            coro = task.get_coro()
            code = getattr(coro, "cr_code", None)
            tasks.append(dict(id=id(task), done=task.done(), cancelled=task.cancelled(),
                function=code.co_qualname if code else type(coro).__name__,
                file=os.path.basename(code.co_filename) if code else None,
                state=getattr(task, "_state", None)))
    emit(event, threads=threads, tasks=tasks,
         loop_closed=loop.is_closed() if loop else None,
         loop_running=loop.is_running() if loop else None,
         fd_count=len(os.listdir('/proc/self/fd')))


emit("child_process_start")
# Import original entrypoint before observing the application. No source rewrite.
from xoc_mcp_lab.verified_server import main
from xoc_mcp_lab import transport

original_next = transport.BoundedLines.__anext__
async def next_line(self):
    try:
        return await original_next(self)
    except StopAsyncIteration:
        snapshot("stdin_eof", asyncio.get_running_loop())
        raise
transport.BoundedLines.__anext__ = next_line

original_flush = transport.BoundedWriter.flush
async def flush(self):
    emit("stdout_flush_begin")
    try:
        return await original_flush(self)
    finally:
        emit("stdout_flush_end")
transport.BoundedWriter.flush = flush

original_serve = transport.serve
async def serve(*args, **kwargs):
    emit("transport_enter")
    try:
        return await original_serve(*args, **kwargs)
    finally:
        snapshot("transport_return", asyncio.get_running_loop())
transport.serve = serve

original_close = asyncio.Runner.close
def close(self):
    loop = self._loop
    snapshot("runner_close_begin", loop)
    try:
        return original_close(self)
    finally:
        snapshot("runner_close_end", loop)
asyncio.Runner.close = close

for method in ("shutdown_asyncgens", "shutdown_default_executor"):
    original = getattr(asyncio.BaseEventLoop, method)
    def build_wrapper(name, delegate):
        async def wrapped(self):
            snapshot(name + "_begin", self)
            try:
                return await delegate(self)
            finally:
                snapshot(name + "_end", self)
        return wrapped
    setattr(asyncio.BaseEventLoop, method, build_wrapper(method, original))

def gc_event(phase, info):
    emit("gc_" + phase, generation=info.get("generation"))

# Enable GC observation only in the exit path, avoiding import-time overhead.
def exit_profile(frame, event, arg):
    if event not in ("call", "return", "c_call", "c_return"):
        return
    if event.startswith("c_"):
        if getattr(arg, "__name__", None) == "flush":
            emit("c_flush_" + event, stream="stdio_or_other")
        return
    module = frame.f_globals.get("__name__", "")
    function = frame.f_code.co_name
    if (module == "threading" and function == "_shutdown" or
        module == "logging" and function == "shutdown" or
        module == "weakref" and function in ("_exitfunc", "__call__") or
        module == "concurrent.futures.thread" and function == "_python_exit" or
        module == "multiprocessing.util" and function == "_exit_function"):
        emit("shutdown_function_" + event, module=module, function=function,
             file=os.path.basename(frame.f_code.co_filename), line=frame.f_lineno)

atexit.register(lambda: emit("atexit_last_observable"))
emit("before_main")
snapshot("main_entered")
try:
    code = main()
finally:
    snapshot("main_returned")
    gc.callbacks.append(gc_event)
    sys.setprofile(exit_profile)
    atexit.register(lambda: snapshot("atexit_first_observable"))
    emit("interpreter_shutdown_begin")
raise SystemExit(code)
