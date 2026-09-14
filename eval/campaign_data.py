"""Build/cache pinned binary data evidence at a drained controller boundary."""
import hashlib
import json
from pathlib import Path
import re
from eval import campaign_state
from solver import binary_data, data_memory

POLICY = 'binary-data-v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_bound(path, pins):
    raw = path.read_bytes()
    if pins.get(str(path)) != sha(raw):
        raise ValueError('binary data input changed: ' + str(path))
    return raw


def symbol_map(text):
    symbols = {}
    for name, value in re.findall(r'(?m)^\s*([A-Za-z_.$][\w.$]*)\s*=\s*(0x[0-9a-fA-F]+)\s*;', text):
        address = int(value, 16)
        if name in symbols and symbols[name] != address:
            raise ValueError('conflicting binary symbol address: ' + name)
        symbols[name] = address
    return symbols


def prepare(state, repo, run):
    """Caller holds campaign lock; no inflight job may change its evidence key."""
    if state.get('fast_inflight') or state.get('inflight'):
        record = state.get('binary_data_catalog')
        if not record:
            return None, []
        raw = (Path(run)/record['path']).read_bytes()
        if sha(raw) != record['sha256']:
            raise ValueError('binary data cache checksum mismatch')
        return json.loads(raw), []
    repo, run = Path(repo), Path(run)
    pins = state['pins']
    files = sorted(Path(p) for p in pins if Path(p).is_relative_to(repo/'asm')
                   and p.endswith(('.data.s', '.rodata.s', '.bss.s', '.sdata.s', '.sbss.s')))
    rom_path, symbols_path = repo/'snowboardkids.z64', repo/'symbol_addrs.txt'
    if not files or str(rom_path) not in pins or str(symbols_path) not in pins:
        if state.get('binary_data_catalog'):
            raise ValueError('previously bound binary data inputs unavailable')
        return None, []
    targets = {name:repo/'nonmatchings'/name/'target.s' for name in state['nodes']}
    targets = {name:p for name,p in targets.items() if str(p) in pins}
    inputs = {str(p): pins[str(p)] for p in [*files, rom_path, symbols_path, *targets.values()]}
    inputs.update({str(Path(module.__file__)):sha(Path(module.__file__).read_bytes())
                   for module in (binary_data, data_memory)})
    inputs[str(Path(__file__))] = sha(Path(__file__).read_bytes())
    input_id = binary_data.identity({'policy': POLICY, 'inputs': inputs})
    record = state.get('binary_data_catalog') or {}
    path = run/'binary-data'/(input_id+'.json')
    if record.get('input_sha256') == input_id and record.get('path') == str(path.relative_to(run)):
        raw = path.read_bytes()
        if sha(raw) != record['sha256']:
            raise ValueError('binary data cache checksum mismatch')
        bundle = json.loads(raw)
    else:
        assemblies = {str(p):read_bound(p,pins).decode() for p in files}
        rom = read_bound(rom_path,pins)
        symbols = symbol_map(read_bound(symbols_path,pins).decode())
        catalog = binary_data.build(assemblies, pins, rom=rom, rom_sha256=pins[str(rom_path)],
                                    symbols=symbols, symbols_sha256=binary_data.identity(symbols))
        packets, memories = {}, {}
        for name, target in targets.items():
            assembly = read_bound(target,pins).decode()
            packet = binary_data.packet(catalog, name, assembly, pins[str(target)], max_chars=3500)
            memory = data_memory.select(catalog, assembly, pins[str(target)])
            if packet['regions']:
                packets[name] = packet
            if memory['regions']:
                memories[name] = memory
        bundle = {'policy':POLICY, 'input_sha256':input_id, 'catalog':catalog,
                  'packets':packets, 'memory':memories,
                  'summary':{**catalog['counts'], 'functions_with_data_context':len(packets),
                             'functions_with_readonly_inputs':len(memories),
                             'rom_bytes':len(rom), 'scope':'pinned named data assembly files; emitted spans, not all ROM assets or reconstructed C'}}
        path.parent.mkdir(parents=True,exist_ok=True)
        campaign_state.atomic(path,bundle)
        record = {'policy':POLICY, 'input_sha256':input_id, 'path':str(path.relative_to(run)),
                  'sha256':sha(path.read_bytes()), 'catalog_sha256':catalog['catalog_sha256'],
                  'summary':bundle['summary']}
    state['binary_data_catalog'] = record
    changed = []
    for name,node in state['nodes'].items():
        if node['status'] in {'object_exact','integrated'}:
            continue
        packet, memory = bundle['packets'].get(name), bundle['memory'].get(name)
        key = binary_data.identity({'packet':packet,'memory':memory}) if packet or memory else None
        if node.get('data_evidence_sha256') != key:
            if key:
                node['data_evidence_sha256'] = key
            else:
                node.pop('data_evidence_sha256',None)
            changed.append(name)
    return bundle, changed


def worker_context(config, bundle, function):
    result = dict(config)
    if bundle:
        if function in bundle['packets']:
            result['binary_data_context'] = {function:bundle['packets'][function]}
        if function in bundle['memory']:
            result['binary_data_memory'] = {function:bundle['memory'][function]}
    return result


def prompt(config, function):
    packet = config.get('binary_data_context',{}).get(function)
    return ('\nBINARY DATA OBSERVATIONS (addresses and bytes are evidence; types and runtime state are not inferred):\n'
            + json.dumps(packet, sort_keys=True)) if packet else ''
