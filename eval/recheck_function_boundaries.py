"""Read-only replay of saved function-boundary inputs; never updates a campaign.

Run under WSL with --inputs a JSON name->artifact mapping (target, candidate,
assembly, address, size, cand_sha) and --output a new report path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile

from solver import byte_certificate as cert, function_boundary as boundary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    inputs_bytes = args.inputs.read_bytes()
    rows = json.loads(inputs_bytes)
    report = {'inputs_sha256': cert.digest(inputs_bytes), 'campaign_mutated': False,
              'checker_sha256': cert.digest(Path(boundary.__file__).read_bytes()), 'functions': {}}
    with tempfile.TemporaryDirectory(prefix='boundary-controls-') as temp:
        root = Path(temp)
        symbols = (args.repo / 'symbol_addrs.txt').read_text()
        for name, row in rows.items():
            result = {'previous_status': row.get('status')}
            report['functions'][name] = result
            candidate = Path(row['candidate'])
            if cert.digest(candidate.read_bytes()) != row['cand_sha']:
                raise ValueError(f'{name}: saved candidate hash changed')
            kwargs = dict(target=Path(row['target']), candidate=candidate,
                          assembly=Path(row['assembly']), rom=args.repo / 'snowboardkids.z64',
                          config=args.repo / 'snowboardkids.yaml', symbols=args.repo / 'symbol_addrs.txt',
                          function=name, address=row['address'], size=row['size'])
            receipt = boundary.certify(**kwargs)
            result['receipt'] = receipt
            result['revalidated'] = boundary.revalidate(receipt) if receipt['function_exact'] else False
            # Frontend/source lineage is deliberately separate from byte certification.
            sidecar = candidate.with_suffix('.verification.json')
            if sidecar.exists():
                saved = json.loads(sidecar.read_text())
                result['saved_frontend'] = saved.get('frontend')
                result['saved_source_sha256'] = saved.get('candidate_source_sha256')
                source = candidate.with_suffix('.c')
                result['saved_frontend_compile_source_bound'] = (source.exists() and
                    saved.get('candidate_sha256') == row['cand_sha'] and
                    (saved.get('frontend') or {}).get('source_sha256') ==
                    saved.get('source_sha256') == cert.digest(source.read_text().encode()))
            if not receipt['function_exact']:
                continue
            shifted = root / 'shifted.txt'
            shifted.write_text(re.sub(r'(?m)^(\s*([\w.$]+)\s*=\s*)(0x[0-9a-fA-F]+)(\s*;)',
                lambda m: m[0] if m[2] == name else m[1] + hex(int(m[3], 16) + 16) + m[4], symbols))
            result['shifted_symbols_accepted'] = boundary.certify(**(kwargs | {'symbols': shifted}))['function_exact']
            missing = root / 'missing.txt'
            missing.write_text(f'{name} = {row["address"]:#x};\n')
            result['missing_symbols_accepted'] = boundary.certify(**(kwargs | {'symbols': missing}))['function_exact']
            # Preserve the annotations; wrong metadata must not select neighboring bytes.
            result['wrong_address_accepted'] = boundary.certify(**(kwargs | {'address': row['address'] + 4}))['function_exact']
            wrong_asm = root / 'wrong.s'
            wrong_asm.write_text(re.sub(r'/\*\s*([0-9A-Fa-f]+)\s+',
                lambda m: '/* ' + format(int(m[1], 16) + 4, 'X') + ' ', kwargs['assembly'].read_text()))
            result['wrong_rom_offset_accepted'] = boundary.certify(**(kwargs | {'assembly': wrong_asm}))['function_exact']
            relocations = cert.object_image(candidate.read_bytes())['sections']['.text']['relocations']
            has_external = any(identity[0] == 'external' and identity[1] != name
                               for _, _, identity in relocations)
            result['external_symbol_controls_applicable'] = has_external
            assert not result['wrong_address_accepted'] and not result['wrong_rom_offset_accepted'], name
            if has_external:
                assert not result['shifted_symbols_accepted'] and not result['missing_symbols_accepted'], name
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({name: {'exact': r['receipt']['function_exact'],
        'error': r['receipt'].get('error'), 'frontend_passed': (r.get('saved_frontend') or {}).get('passed'),
        'negative_accepted': [k for k, v in r.items() if k.endswith('_accepted') and v]}
        for name, r in report['functions'].items()}, indent=2))


if __name__ == '__main__':
    main()
