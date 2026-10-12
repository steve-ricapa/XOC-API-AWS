"""IC-1R.5 diagnostic observer only. GDB/MI2, bounded phases, fail-safe detach.

Never imported by XOC/MCP. Only an owned, identity-pinned innocuous target is
eligible until qualification O1-O8. Output is a strict technical allowlist.
"""
import ast
import json
import os
from pathlib import Path
import re
import queue
import select
import subprocess
import sys
import threading
import time

ATTACH_SECONDS = .100
STACK_SECONDS = .100
DETACH_SECONDS = .075
INTERACTION_SECONDS = .300
SYMBOL = re.compile(r'[A-Za-z_][A-Za-z0-9_:.<>~* +\-]{0,180}\Z')
SAFE_WORD = re.compile(r'[A-Za-z0-9_.+\-]{1,200}\Z')


class MIParseError(Exception):
    pass


class PhaseTimeout(Exception):
    pass


class MIParser:
    """Small bounded recursive parser of MI result/async records, not CLI text."""
    def __init__(self, text):
        if len(text) > 131072:
            raise MIParseError('oversized record')
        self.text, self.pos = text, 0

    def name(self):
        found = re.match(r'[A-Za-z_][A-Za-z0-9_\-]*', self.text[self.pos:])
        if not found:
            raise MIParseError('invalid name')
        self.pos += len(found[0])
        return found[0]

    def value(self, depth=0):
        if depth > 20 or self.pos >= len(self.text):
            raise MIParseError('invalid nesting')
        char = self.text[self.pos]
        if char == '"':
            start = self.pos
            self.pos += 1
            while self.pos < len(self.text):
                char = self.text[self.pos]
                self.pos += 1
                if char == '\\':
                    self.pos += 1
                elif char == '"':
                    try:
                        return ast.literal_eval(self.text[start:self.pos])
                    except (ValueError, SyntaxError):
                        raise MIParseError('invalid quoted constant') from None
            raise MIParseError('unterminated constant')
        if char in '{[':
            self.pos += 1
            end = '}' if char == '{' else ']'
            items = []
            while self.pos < len(self.text) and self.text[self.pos] != end:
                if self.text[self.pos] in '"{[':
                    items.append(self.value(depth + 1))
                else:
                    name = self.name()
                    if self.text[self.pos:self.pos+1] != '=':
                        raise MIParseError('missing equals')
                    self.pos += 1
                    items.append((name, self.value(depth + 1)))
                if self.text[self.pos:self.pos+1] == ',':
                    self.pos += 1
                elif self.text[self.pos:self.pos+1] != end:
                    raise MIParseError('missing delimiter')
            if self.text[self.pos:self.pos+1] != end:
                raise MIParseError('unterminated collection')
            self.pos += 1
            if char == '{':
                if not all(isinstance(v, tuple) for v in items):
                    raise MIParseError('invalid tuple')
                return dict(items)
            return items
        raise MIParseError('invalid value')

    @classmethod
    def record(cls, line):
        if line in ('', '(gdb)', '(gdb) '):
            return None
        # Stream records may include source/data. Discard without decoding.
        if line[0] in '~@&':
            return dict(kind='stream', category='suppressed', data={}, command_id=None)
        found = re.match(r'(\d*)([\^*=+])([A-Za-z0-9_-]+)', line)
        if not found:
            raise MIParseError('unrecognized record')
        parser = cls(line)
        parser.pos = found.end()
        data = {}
        while parser.pos < len(line):
            if line[parser.pos] != ',':
                raise MIParseError('invalid result delimiter')
            parser.pos += 1
            name = parser.name()
            if line[parser.pos:parser.pos+1] != '=':
                raise MIParseError('missing equals')
            parser.pos += 1
            data[name] = parser.value()
        return dict(kind=found[2], category=found[3], data=data,
                    command_id=int(found[1]) if found[1] else None)


def identity(pid):
    root = Path('/proc') / str(pid)
    stat = (root/'stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, ppid=int(stat[1]), start_ticks=int(stat[19]),
                executable=os.readlink(root/'exe'), uid=root.stat().st_uid)


def state(pid):
    try:
        raw = (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()
        status = dict(line.split(':',1) for line in (Path('/proc')/str(pid)/'status').read_text().splitlines())
        return dict(state=raw[0], start_ticks=int(raw[19]), tracer_pid=int(status['TracerPid'].strip()))
    except (FileNotFoundError, ProcessLookupError):
        return dict(state='EXITED', tracer_pid=0)


def safe_frame(frame):
    if not isinstance(frame, dict):
        return None
    symbol = frame.get('func', '')
    module = os.path.basename(frame.get('from', ''))
    level = frame.get('level', '0')
    if not str(level).isdigit() or int(level) > 11:
        return None
    return dict(frame=int(level), symbol=symbol if SYMBOL.fullmatch(symbol) else None,
                library=module if SAFE_WORD.fullmatch(module) else None)


class Observer:
    def __init__(self, binding, event_path=None):
        self.binding = binding
        self.target_pid = binding['target']['pid']
        self.proc = None
        self.buffer = b''
        self.next_id = 0
        self.current_phase = 'prepare'
        self.frames = []
        self.lock = threading.Lock()
        self.stop_seen = None
        self.detach_ack = False
        self.attach_ack = False
        self.attach_sent = False
        self.inject = None
        self.injected = False
        self.channel_cut = False
        self.watchdog_done = threading.Event()
        self.watchdog_fired = False
        self.observer_killed = False
        self.timeouts = 0
        self.timings = {}
        self.phase_sent = {}
        self.stop_samples = []
        self.event_file = event_path.open('x',encoding='utf-8') if event_path else None
        self.output_queue = queue.Queue(maxsize=4096) if event_path else None
        self.output_thread = None
        if self.output_queue is not None:
            self.output_thread=threading.Thread(target=self.forward_events,daemon=True)
            self.output_thread.start()

    def forward_events(self):
        while True:
            line=self.output_queue.get()
            if line is None:
                return
            print(line,flush=True)

    def emit(self, event, **fields):
        # All callers pass technical, typed fields only. No MI raw strings.
        row = dict(mono=time.monotonic(), generation=self.binding['generation'],
                   observer_pid=os.getpid(), target_pid=self.target_pid, event=event, **fields)
        with self.lock:
            line=json.dumps(row,separators=(',',':'))
            if self.event_file:
                # Persist NOW in owned tmpfs; don't block MI processing on the
                # Docker/Windows/OneDrive forwarding path. Host also flushes
                # each forwarded line; no raw MI ever enters this journal.
                self.event_file.write(line+'\n')
                self.event_file.flush()
                self.output_queue.put_nowait(line)
            else:
                print(line,flush=True)

    def validate(self, requested):
        for field in ('generation','container_id','container_label','target','parent_start'):
            if requested.get(field) != self.binding.get(field):
                self.emit('identity_rejected', status=field)
                return False
        try:
            current = identity(self.target_pid)
            if current != self.binding['target'] or current['ppid'] != 1:
                self.emit('identity_rejected', status='proc_mismatch')
                return False
            if current['executable'] != '/opt/xoc-l2/bin/python' or current['uid'] != 10001:
                self.emit('identity_rejected', status='executable_or_owner')
                return False
            if identity(1)['start_ticks'] != self.binding['parent_start']:
                self.emit('identity_rejected', status='parent_reused')
                return False
        except (FileNotFoundError, ProcessLookupError):
            self.emit('target_exited_before_attach')
            return False
        self.emit('identity_validated', start_ticks=current['start_ticks'])
        return True

    def prepare(self):
        self.proc = subprocess.Popen(['/usr/bin/gdb','-q','-nx','-nh','--readnever','--interpreter=mi2',
            '-iex','set auto-load off'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0)
        self.gdb_identity = identity(self.proc.pid)
        self.emit('observer_started', gdb_pid=self.proc.pid)
        for command in ('-gdb-set pagination off','-gdb-set confirm off',
            '-gdb-set print frame-arguments none','-gdb-set print entry-values no',
            '-gdb-set auto-solib-add on',
            '-gdb-set sysroot /proc/1/root',
            '-file-exec-and-symbols /proc/1/root/opt/xoc-l2/bin/python'):
            result = self.command(command, 'prepare', 5.0)
            if result['category'] != 'done':
                raise RuntimeError('prepare_failed')
        self.emit('observer_ready', protocol='mi2')

    def send(self, command, phase):
        self.next_id += 1
        command_id = self.next_id
        self.current_phase = phase
        self.phase_sent[command_id] = time.monotonic()
        # Persist BEFORE writing. No command parameters go into evidence.
        self.emit('command_sent', command_id=command_id, phase=phase,
                  command=command.split()[0])
        self.proc.stdin.write((str(command_id)+command+'\n').encode())
        return command_id

    def consume(self, line):
        record = MIParser.record(line)
        if record is None:
            return None
        if record['kind'] == 'stream':
            return record
        fields = dict(command_id=record['command_id'], status=record['category'], phase=self.current_phase)
        self.emit('mi_record', **fields)
        data = record['data']
        if record['category']=='library-loaded':
            library=os.path.basename(data.get('target-name',''))
            self.emit('library_loaded',library=library if SAFE_WORD.fullmatch(library) else None)
        if record['category'] == 'stopped':
            self.stop_seen = time.monotonic()
            self.emit('target_stopped', thread_id=data.get('thread-id') if str(data.get('thread-id','')).isdigit() else None)
        frames = []
        if isinstance(data.get('frame'), dict):
            frames.append(data['frame'])
        for item in data.get('stack', []):
            if isinstance(item, tuple) and item[0] == 'frame':
                frames.append(item[1])
            elif isinstance(item, dict):
                frames.append(item)
        for frame in frames:
            safe = safe_frame(frame)
            if safe is not None:
                self.frames.append(safe)
                self.emit('frame', command_id=record['command_id'], **safe)
        if self.inject == 'parser' and self.current_phase == 'stack' and not self.injected:
            self.injected = True
            self.emit('injected_error', status='parser')
            raise MIParseError('injected')
        return record

    def read_records(self, seconds, tolerant=False):
        if self.channel_cut:
            self.watchdog_done.wait(seconds)
            return []
        readable, _, _ = select.select([self.proc.stdout], [], [], seconds)
        if not readable:
            return []
        chunk = os.read(self.proc.stdout.fileno(), 65536)
        if not chunk:
            self.emit('observer_eof')
            raise EOFError('gdb_eof')
        self.buffer += chunk
        if len(self.buffer) > 262144:
            raise MIParseError('buffer_limit')
        result = []
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                record = self.consume(line.decode('utf-8',errors='replace').rstrip('\r'))
                if record:
                    result.append(record)
            except MIParseError:
                self.emit('mi_parse_error', phase=self.current_phase)
                if not tolerant:
                    raise
        return result

    def command(self, command, phase, seconds, tolerant=False):
        sent = time.monotonic()
        command_id = self.send(command, phase)
        while time.monotonic()-sent < seconds:
            for record in self.read_records(min(.005, max(.0001,seconds-(time.monotonic()-sent))), tolerant):
                if record['kind']=='^' and record['command_id']==command_id:
                    if self.inject=='timeout' and phase=='stack':
                        self.injected = True
                        continue
                    elapsed = (time.monotonic()-sent)*1000
                    self.timings[phase+'_ms'] = elapsed
                    self.emit('command_acknowledged', command_id=command_id, phase=phase,
                              status=record['category'], wall_ms=elapsed)
                    if record['category']=='error':
                        message = str(record['data'].get('msg',''))
                        if 'Operation not permitted' in message:
                            self.emit('permission_denied', phase=phase)
                    return record
        self.timeouts += 1
        self.emit('phase_timeout', command_id=command_id, phase=phase,
                  wall_ms=(time.monotonic()-sent)*1000)
        raise PhaseTimeout(phase)

    def terminate_gdb(self):
        if self.proc.poll() is not None:
            return
        try:
            if identity(self.proc.pid) != self.gdb_identity:
                self.emit('observer_identity_rejected')
                return
        except (FileNotFoundError, ProcessLookupError):
            return
        self.emit('observer_terminate_sent', gdb_pid=self.proc.pid)
        self.proc.terminate()
        try:
            self.proc.wait(timeout=.030)
        except subprocess.TimeoutExpired:
            # Only the identity-pinned GDB process, NEVER the target.
            self.emit('observer_kill_sent', gdb_pid=self.proc.pid)
            self.proc.kill()
            self.proc.wait(timeout=.100)
        self.observer_killed = True

    def watchdog(self):
        deadline=time.monotonic()+INTERACTION_SECONDS
        while not self.watchdog_done.is_set() and time.monotonic()<deadline:
            observed=state(self.target_pid)
            self.stop_samples.append((time.monotonic(),observed['state']))
            self.watchdog_done.wait(.001)
        if not self.watchdog_done.is_set():
            self.watchdog_fired = True
            self.emit('observer_watchdog_fired')
            self.terminate_gdb()

    def recover(self):
        if self.attach_sent:
            try:
                result = self.command('-target-detach', 'detach', DETACH_SECONDS, tolerant=True)
                self.detach_ack = result['category']=='done'
                if self.detach_ack:
                    self.emit('detach_acknowledged')
            except (PhaseTimeout, EOFError, OSError, MIParseError):
                self.emit('detach_unacknowledged')
            if not self.detach_ack:
                self.terminate_gdb()
        after = state(self.target_pid)
        self.stop_samples.append((time.monotonic(),after['state']))
        self.emit('target_after_detach', **after)
        self.watchdog_done.set()
        return after

    def observe(self, requested, inject=None, race=None):
        self.inject = inject
        if not self.validate(requested):
            return dict(status='IDENTITY_REJECTED' if state(self.target_pid)['state']!='EXITED' else 'TARGET_EXITED',
                        attach_sent=False, timings={})
        begun = time.monotonic()
        watch = threading.Thread(target=self.watchdog, daemon=True)
        watch.start()
        status = 'UNKNOWN'
        try:
            self.attach_sent = True
            attached = self.command('-target-attach '+str(self.target_pid),'attach',ATTACH_SECONDS)
            if attached['category'] != 'done':
                status = 'ATTACH_FAILED'
            else:
                self.attach_ack = True
                self.emit('attach_acknowledged')
                if race in ('during_stack','before_detach'):
                    # In all-stop mode a target cannot exit naturally while
                    # traced. Release it first, then exercise a late command
                    # at that boundary. No target signal or second attach.
                    early=self.command('-target-detach','race_release',DETACH_SECONDS)
                    if early['category']!='done':
                        raise RuntimeError('race_release_failed')
                    self.emit('race_target_released',status=race)
                    self.watchdog_done.set()
                    end=time.monotonic()+1.0
                    while state(self.target_pid)['state'] not in ('EXITED','Z'):
                        if time.monotonic()>=end:
                            raise PhaseTimeout('natural_exit')
                        time.sleep(.001)
                    late=self.command('-stack-list-frames 0 11' if race=='during_stack' else '-target-detach',
                                      'race_late_command',STACK_SECONDS)
                    self.emit('natural_exit_race',status=late['category'])
                    status='TARGET_EXITED_RACE'
                    return_after_race=True
                else:
                    return_after_race=False
                if inject == 'channel_cut':
                    self.injected = True
                    self.proc.stdout.close()
                    self.channel_cut = True
                    self.emit('injected_error', status='channel_cut')
                    raise EOFError('injected')
                if not return_after_race:
                    stack = self.command('-stack-list-frames 0 11','stack',STACK_SECONDS)
                    status = 'CAPTURED' if stack['category']=='done' and self.frames else 'STACK_INCOMPLETE'
        except (PhaseTimeout, MIParseError, EOFError, OSError) as error:
            status = type(error).__name__
            self.emit('observation_error', status=status)
        finally:
            after = self.recover()
            watch.join(timeout=.150)
        total = (time.monotonic()-begun)*1000
        # MI stopped can arrive late (after library processing); never use that
        # timestamp as an upper bound of the kernel tracing-stop duration.
        traced=[i for i,(_,s) in enumerate(self.stop_samples) if s in ('t','T')]
        lower=upper=None
        if traced:
            first,last=traced[0],traced[-1]
            lower=(self.stop_samples[last][0]-self.stop_samples[first][0])*1000
            released_samples=[stamp for stamp,s in self.stop_samples[last+1:] if s not in ('t','T')]
            prior=self.stop_samples[first-1][0] if first else begun
            upper=((released_samples[0] if released_samples else time.monotonic())-prior)*1000
        self.timings.update(total_ms=total,stop_lower_bound_ms=lower,
            stop_upper_bound_ms=upper,stop_sampling_ms=1.0)
        return dict(status=status, attach_sent=self.attach_sent, attach_ack=self.attach_ack,
            detach_ack=self.detach_ack, timings=self.timings, frames=self.frames,
            target_after=after, watchdog_fired=self.watchdog_fired,
            observer_forced_exit=self.observer_killed,timeouts=self.timeouts,injected=self.injected)

    def close(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                self.command('-gdb-exit','exit',.075,tolerant=True)
                self.proc.wait(timeout=.050)
            except (PhaseTimeout, OSError, EOFError, MIParseError, subprocess.TimeoutExpired):
                self.terminate_gdb()
        self.emit('observer_exit', returncode=self.proc.returncode)

    def finish_events(self):
        if self.output_queue is not None:
            self.output_queue.put(None,timeout=1)
            self.output_thread.join(timeout=2)
            self.event_file.close()


def main():
    binding = json.loads(sys.stdin.readline())
    assert identity(1)['start_ticks']==binding['parent_start']
    assert identity(1)['uid']==os.getuid()==10001
    observer = Observer(binding,Path('/proc/1/root/tmp/ic1r5-events.jsonl'))
    try:
        observer.prepare()
        request = json.loads(sys.stdin.readline())
        result = observer.observe(request['requested'],request.get('inject'),request.get('race'))
        observer.emit('observation_result', **result)
    except Exception as error:
        observer.emit('harness_error', status=type(error).__name__)
        if observer.attach_sent:
            observer.recover()
    finally:
        observer.close()
        observer.finish_events()


if __name__ == '__main__':
    main()
