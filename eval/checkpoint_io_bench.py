"""Compare checkpoint encodings on identical data without changing live state."""
import argparse
import json
from pathlib import Path
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with (args.run / 'campaign.json').open() as stream:
        state = json.load(stream)
    report = {'checkpoint_bytes': (args.run / 'campaign.json').stat().st_size,
              'functions': len(state['nodes']), 'results': []}
    with tempfile.TemporaryDirectory(prefix='checkpoint-bench-', dir=args.run) as directory:
        for name, indent, buffering in [('original', 2, -1),
                                        ('buffered_pretty', 2, 1024 * 1024),
                                        ('buffered_compact', None, 1024 * 1024)]:
            path = Path(directory) / (name + '.json')
            start = time.perf_counter()
            with path.open('w', encoding='utf-8', buffering=buffering) as stream:
                json.dump(state, stream, indent=indent,
                          **({'separators': (',', ':')} if indent is None else {}))
                stream.write('\n')
            seconds = time.perf_counter() - start
            with path.open() as stream:
                assert json.load(stream) == state, 'Roundtrip changed checkpoint content'
            row = {'mode': name, 'seconds': round(seconds, 3), 'bytes': path.stat().st_size,
                   'roundtrip_equal': True}
            report['results'].append(row)
            print(json.dumps(row), flush=True)
            path.unlink()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
