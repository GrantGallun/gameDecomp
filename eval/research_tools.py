"""Fixed local research tools. No model-provided commands, URLs, paths or imports."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from tools.synthetic_corpus import DEFAULT_TARGET, features, function_listing, recipe

PRELUDE = ('typedef signed char s8; typedef unsigned char u8;\n'
           'typedef short s16; typedef unsigned short u16;\n'
           'typedef int s32; typedef unsigned int u32;\n'
           'typedef float f32; typedef double f64;\n')


def gateway_address():
    """Only WSL's actual default gateway may reach Windows-hosted Ollama."""
    try:
        if 'microsoft' not in Path('/proc/sys/kernel/osrelease').read_text().lower():
            return None
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            row = line.split()
            if row[1] == '00000000' and int(row[3], 16) & 2:
                return socket.inet_ntoa(struct.pack('<I', int(row[2], 16)))
    except (OSError, ValueError, IndexError):
        pass
    return None


def local_endpoint(url, *, gateway):
    p = urlsplit(url)
    host = p.hostname
    if (p.scheme != 'http' or p.username or p.password or p.path not in ('', '/')
            or p.query or p.fragment or p.port != 11434):
        raise ValueError('Only a local Ollama HTTP endpoint on port 11434 is allowed')
    # No DNS resolution: even a local-looking domain could resolve remotely.
    if host != 'localhost':
        try:
            address = ipaddress.ip_address(host)
        except (ValueError, TypeError) as exc:
            raise ValueError('Ollama must be on loopback or the WSL host gateway') from exc
        if not address.is_loopback and host != gateway:
            raise ValueError('Remote Ollama endpoints are disabled')
    host = '127.0.0.1' if host == 'localhost' else host
    return f'http://[{host}]:11434' if ':' in host else f'http://{host}:11434'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Ollama redirects are disabled')


def request(endpoint, path, payload=None, timeout=8):
    endpoint = local_endpoint(endpoint, gateway=gateway_address())
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(endpoint + path, data=data, headers={'Content-Type': 'application/json'})
    # Never inherit HTTP_PROXY/HTTPS_PROXY or system proxy settings.
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=timeout) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Ollama response exceeded the size limit')
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get('error'):
        raise ValueError(str(result.get('error', 'Invalid Ollama response')) if isinstance(result, dict)
                         else 'Invalid Ollama response')
    return result


def validate_model(name, tags, show):
    if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', name) or 'cloud' in name.lower():
        raise ValueError('Cloud models are disabled; select installed local weights')
    tag = next((t for t in tags.get('models', []) if t.get('name') == name), None)
    if tag is None or not tag.get('size', 0) > 0:
        raise ValueError('The selected model is not installed locally; no automatic downloads')
    for info in (tag, show):
        if any(value for key, value in info.items() if key.startswith('remote')):
            raise ValueError('Ollama cloud/remote aliases are disabled')
        if info.get('details', {}).get('format') != 'gguf':
            raise ValueError('Local GGUF model metadata is required')
    if not show.get('model_info', {}).get('general.architecture'):
        raise ValueError('Local model architecture metadata is missing')
    if not re.fullmatch('[0-9a-f]{64}', tag.get('digest', '')):
        raise ValueError('Local model digest is missing')
    return {'model': name, 'digest': tag['digest'], 'size': tag['size']}


def inspect_model(endpoint, model):
    gateway = gateway_address()
    endpoints = ([local_endpoint(endpoint, gateway=gateway)] if endpoint != 'auto' else
                 ['http://127.0.0.1:11434'] + ([f'http://{gateway}:11434'] if gateway else []))
    errors = []
    for base in endpoints:
        try:
            tags = request(base, '/api/tags')
        except OSError as exc:
            errors.append(str(exc))
            continue
        # Reject a cloud-looking name before even requesting its metadata.
        if 'cloud' in model.lower():
            raise ValueError('Cloud models are disabled')
        show = request(base, '/api/show', {'model': model})
        return {**validate_model(model, tags, show), 'endpoint': base}
    raise RuntimeError('Local Ollama is unavailable: ' + '; '.join(errors)[-600:])


def sha_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def resolve_recipe(repo):
    resolved = recipe(repo, DEFAULT_TARGET)
    paths = [*sorted((repo/'tools/ido-recomp/linux').glob('*')),
             repo/'tools/asm-processor/build.py', repo/'tools/asm-processor/asm_processor.py']
    resolved['tool_hashes'] = {str(p.relative_to(repo)): sha_file(p) for p in paths if p.is_file()}
    resolved['extractor_sha256'] = sha_file(Path(__file__).parents[1]/'tools/synthetic_corpus.py')
    resolved['prelude_sha256'] = hashlib.sha256(PRELUDE.encode()).hexdigest()
    resolved['identity'] = hashlib.sha256(json.dumps(resolved, sort_keys=True).encode()).hexdigest()
    return resolved


def compile_probe(payload):
    import resource
    # The parent also bounds wall time and terminates this entire process group.
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16*1024**2, 16*1024**2))
    os.nice(5)
    work = Path(payload['work'])
    work.mkdir(parents=True, exist_ok=False)
    source, obj = work/'probe.c', work/'probe.o'
    source.write_text(PRELUDE + payload['source'])
    proc = subprocess.run([*payload['recipe']['command'], '-o', str(obj), str(source)],
                          cwd=payload['repo'], capture_output=True, text=True, timeout=50)
    row = {'compiled': proc.returncode == 0 and obj.is_file(),
           'stderr': proc.stderr[-3000:], 'source_sha256': sha_file(source)}
    if row['compiled']:
        dump = subprocess.run(['mips-linux-gnu-objdump', '-dr', '--no-show-raw-insn', str(obj)],
                              capture_output=True, text=True, check=True, timeout=8).stdout
        listing = function_listing(dump, 'syn_probe')
        if not listing:
            return {**row, 'compiled': False, 'stderr': 'No syn_probe symbol in the object'}
        (work/'probe.asm').write_text('\n'.join(listing))
        row.update(features=features(listing), asm='\n'.join(listing), object_sha256=sha_file(obj))
    return row


def dispatch(op, payload):
    if op == 'inspect':
        return inspect_model(payload['endpoint'], payload['model'])
    if op == 'recipe':
        return resolve_recipe(Path(payload['repo']))
    if op == 'chat':
        info = inspect_model(payload['endpoint'], payload['model'])
        if info['digest'] != payload['digest']:
            raise ValueError('Model weights changed during this run; start a new run')
        body = {k: payload[k] for k in ('model', 'messages', 'format')}
        body.update(stream=False, keep_alive='1m',
                    options={'num_ctx': 8192, 'num_predict': 2048, 'temperature': 0.4})
        body['think'] = 'low' if payload['model'].startswith('gpt-oss') else False
        return request(payload['endpoint'], '/api/chat', body, timeout=175)
    if op == 'compile':
        return compile_probe(payload)
    raise ValueError('Unknown research tool')


if __name__ == '__main__':
    try:
        result = dispatch(sys.argv[1], json.load(sys.stdin))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        raise SystemExit(1)
