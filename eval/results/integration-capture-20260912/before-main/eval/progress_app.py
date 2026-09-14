"""Small localhost dashboard and pause/resume control for the live campaign.

Reads the compact checkpoint pointer, bounded log tail, and an incremental
read-only projection of checkpoint objects; it never scans the attempt database.
The only writes it can perform are the campaign
service's own `pause` and `resume`, through WSL, on the run it was started
with: no other command, argument or path comes from the request. Pause is
durable and stops work at a work-item boundary; neither control edits a
checkpoint, and acceptance gates are untouched.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import csv
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit, parse_qs
from eval.progress_map import MapFeed

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def recent_results(path, limit=40):
    if not path.exists():
        return []
    with path.open('rb') as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell()-128*1024))
        lines = stream.read().decode('utf-8', errors='replace').splitlines()
    results = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict) and isinstance(row.get('function'), str):
            results.append({k:row.get(k) for k in ('function','profile','status','score','performance')})
        if len(results) == limit:
            break
    return results


def snapshot(run, now=None):
    now = time.time() if now is None else now
    checkpoint = read_json(run/'campaign.json')
    service = read_json(run/'service.json')
    if checkpoint.get('kind') != 'campaign-checkpoint-index-v1':
        raise ValueError('This dashboard expects the compact campaign checkpoint.')
    health = checkpoint.get('health', {})
    status = service.get('status', 'unknown')
    control_path = run/'service-control.json'
    control = read_json(control_path) if control_path.exists() else {}
    heartbeat = service.get('heartbeat_at')
    heartbeat_age = max(0, now-heartbeat) if heartbeat else None
    if control.get('paused') or (run/'service.pause').exists():
        status = 'pausing' if service.get('worker_pid') else 'paused'
    elif status in {'running','restart','retry'} and (heartbeat_age is None or heartbeat_age > 45):
        status = 'heartbeat_delayed'
    workers = []
    for index, row in enumerate(health.get('parallel_inflight', [])):
        try:
            started = int(row['id'].split('-',1)[0])/1e9
        except (KeyError, ValueError):
            started = None
        profile = row.get('profile') or {}
        workers.append({'function':row.get('function'), 'profile':profile.get('name'),
                        'model':bool(profile.get('model')), 'lane':profile.get('lane'),
                        'elapsed_seconds':max(0,now-started) if started else None})
    # "Inflight" includes a result waiting for ordered import, not a guaranteed
    # live CPU process. Use that language in the UI.
    metrics = dict(checkpoint.get('fast_metrics',{}))
    anchor = metrics.get('session_live_since')
    if (anchor is not None and metrics.get('session_pid') == service.get('worker_pid')
            and status in {'running','pausing'} and heartbeat_age is not None and heartbeat_age <= 45):
        metrics['session_seconds'] = metrics.get('session_seconds',0.)+max(0.,now-anchor)
        for label, count in [('improvements','improved_items'),('exact','exact_items')]:
            metrics[label+'_per_hour'] = 3600*metrics.get(count,0)/max(1.,metrics['session_seconds'])
    return {'app':'gameDecomp-progress', 'run':run.name, 'now':now,
            'status':status,'heartbeat_age':heartbeat_age,
            'checkpoint_age':max(0,now-checkpoint.get('updated_at',now)),
            'commit':checkpoint.get('commit'), 'functions':health.get('functions',0),
            'states':health.get('states',{}),'semantics':health.get('semantic_functions',{}),
            'summary':checkpoint.get('summary',{}), 'metrics':metrics,
            'workers':workers, 'recent':recent_results(run/'pipeline.log'),
            'completed_batches':service.get('completed_batches',0),
            'error':service.get('error') if status == 'needs_repair' else None}


class Feed:
    def __init__(self,run):
        self.run, self.lock, self.cached, self.last = run, threading.Lock(), None, 0
        self.gpu = {'available':False}
        self.map = MapFeed(run)

    def monitor_gpu(self):
        while True:
            self.gpu = gpu_sample()
            time.sleep(5)

    def get(self):
        with self.lock:
            if self.cached is None or time.monotonic()-self.last >= 1:
                self.cached = snapshot(self.run)
                self.cached['gpu'] = self.gpu
                self.last = time.monotonic()
            return self.cached


def gpu_sample():
    """Collect off the HTTP thread; never confuse allocated VRAM with activity."""
    try:
        result = subprocess.run(['nvidia-smi',
            '--query-gpu=name,utilization.gpu,memory.used,memory.total,power.draw,power.limit',
            '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3,
            check=True, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        row = next(csv.reader(result.stdout.splitlines(), skipinitialspace=True))
        values = dict(zip(('utilization','memory_used_mib','memory_total_mib','power_watts','power_limit_watts'),
                          (float(v) if v not in {'[N/A]','N/A','[Not Supported]'} else None for v in row[1:])))
        return {'available':True,'name':row[0],'sampled_at':time.time(),**values}
    except (OSError,subprocess.SubprocessError,ValueError,StopIteration):
        return {'available':False,'sampled_at':time.time()}


def wsl_path(path):
    """C:\\Code\\x -> /mnt/c/Code/x, matching the campaign's own WSL invocations.

    A path that is already POSIX-absolute is passed through, so the same code
    serves from Windows and runs under WSL. Anything else -- a UNC share, a
    drive-relative path -- is refused rather than guessed at.
    """
    text = str(path)
    if text.startswith('\\\\') or text.startswith('//'):
        raise ValueError('a network path has no WSL equivalent here')
    if not re.match(r'^[A-Za-z]:[\\/]', text) and not text.startswith('/'):
        text = str(Path(text).resolve())
    if text.startswith('/'):
        return PurePosixPath(text).as_posix()
    drive = re.match(r'^([A-Za-z]):[\\/]', text)
    if not drive:
        raise ValueError(f'cannot express {text!r} as a WSL path')
    return '/mnt/' + drive.group(1).lower() + PureWindowsPath(text).as_posix()[2:]


class Control:
    """Serialized pause/resume. One command at a time, bounded, no shell."""

    def __init__(self, run, distro, python, script, timeout=120):
        self.command = {action: ['wsl.exe', '-d', distro, '-e', python, wsl_path(script),
                                 '--run', wsl_path(run), action]
                        for action in ('pause', 'resume')}
        self.timeout = timeout
        self.lock = threading.Lock()

    def __call__(self, action):
        if action not in self.command:
            raise ValueError('action must be pause or resume')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('another control command is still running')
        try:
            done = subprocess.run(self.command[action], capture_output=True, text=True,
                                  timeout=self.timeout)
        except subprocess.SubprocessError as exc:
            raise RuntimeError(f'control command failed: {exc}') from exc
        finally:
            self.lock.release()
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout or 'control command failed').strip()[-400:])
        return done.stdout.strip()[-2000:]


def handler(feed, control=None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            route = urlsplit(self.path).path
            # Same-origin only: a custom header cannot be sent cross-origin
            # without a preflight this server never approves.
            origin = self.headers.get('Origin')
            allowed = {f'http://127.0.0.1:{self.server.server_address[1]}',
                       f'http://localhost:{self.server.server_address[1]}'}
            try:
                if route != '/api/control' or control is None:
                    self.send_error(404); return
                if self.headers.get('X-Campaign-Control') != 'ui' or (origin and origin not in allowed):
                    self.send_error(403, 'Cross-origin control is refused'); return
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= 1024:
                    self.send_error(400, 'Unexpected control body'); return
                action = json.loads(self.rfile.read(length)).get('action')
                body = json.dumps({'action': action, 'output': control(action),
                                   'status': feed.get().get('status')}).encode()
                code = 200
            except (ValueError, TypeError) as exc:
                code, body = 400, json.dumps({'error': str(exc)}).encode()
            except (RuntimeError, OSError) as exc:
                code, body = 503, json.dumps({'error': str(exc)}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path).path
            try:
                if route == '/api/status':
                    body = json.dumps(feed.get()).encode()
                    content = 'application/json; charset=utf-8'
                elif route in {'/api/map', '/api/function'}:
                    name = parse_qs(urlsplit(self.path).query).get('name', [''])[0] if route == '/api/function' else None
                    try:
                        body = json.dumps(feed.map.get(name)).encode()
                    except KeyError:
                        self.send_error(404, 'Unknown campaign function'); return
                    content = 'application/json; charset=utf-8'
                elif route == '/progress_map.js':
                    body = (ROOT/'eval/progress_map.js').read_bytes()
                    content = 'text/javascript; charset=utf-8'
                elif route == '/':
                    body = (ROOT/'eval/progress_app.html').read_bytes()
                    content = 'text/html; charset=utf-8'
                elif route == '/favicon.ico':
                    self.send_response(204); self.end_headers(); return
                else:
                    self.send_error(404); return
                self.send_response(200)
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.send_response(503)
                body = json.dumps({'error':f'Progress data unavailable: {exc}'}).encode()
                content = 'application/json; charset=utf-8'
            self.send_header('Content-Type',content)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self,*args):
            pass
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=ROOT/'eval/results/resume-pipeline-20260908')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--wsl-distro',default='Ubuntu')
    parser.add_argument('--wsl-python',default='/home/grant/decomp/sbk1/.venv/bin/python')
    parser.add_argument('--read-only',action='store_true',help='serve without pause/resume control')
    args=parser.parse_args()
    feed=Feed(args.run.resolve())
    control=None if args.read_only else Control(
        args.run.resolve(), args.wsl_distro, args.wsl_python, ROOT/'eval/campaign_service.py')
    threading.Thread(target=feed.monitor_gpu,daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(feed,control))
    print(f'gameDecomp progress: http://127.0.0.1:{args.port}'
          f"{' (read-only)' if control is None else ''}",flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
