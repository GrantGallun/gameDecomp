"""Apply existing clean-set identity rules to a fast rg file inventory on Windows."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import sys
import time

PROJECT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
from eval import clean_set


def identities(raw):
    path = PROJECT / raw.strip()
    names = set()
    try:
        if path.suffix == '.jsonl':
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    if line.strip():
                        names.update(clean_set._names_in_value(json.loads(line)))
        else:
            names.update(clean_set._names_in_value(json.loads(path.read_text(encoding='utf-8'))))
        return names, None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return names, {'file': str(path.relative_to(PROJECT)), 'error': type(exc).__name__}


def main():
    started = time.monotonic()
    paths = (HERE / 'history-paths.txt').read_text(encoding='utf-8-sig').splitlines()
    names, unavailable = set(), []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, (found, error) in enumerate(pool.map(identities, paths), 1):
            names.update(found)
            if error:
                unavailable.append(error)
            if index % 10000 == 0:
                print(json.dumps({'audited_artifacts': index, 'excluded_names': len(names)}), flush=True)
    names.update(clean_set._set_names(PROJECT / 'eval/sets'))
    names.update(clean_set._recovered_names(PROJECT))
    payload = {'names': sorted(names), 'source': 'Existing eval.clean_set identity parser, sets and recovered-source exclusions; rg --files --hidden --no-ignore inventory',
               'files': len(paths), 'inventory_sha256': hashlib.sha256((HERE / 'history-paths.txt').read_bytes()).hexdigest(),
               'unreadable_or_partial_artifacts': unavailable,
               'seconds': time.monotonic() - started, 'created_at': time.time(),
               'limits': 'Historical JSON/result/set/recovered membership only; no claim about unrecorded human exposure or pretraining.'}
    (HERE / 'history-names.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'complete': True, 'artifacts': len(paths), 'excluded_names': len(names), 'unavailable': len(unavailable), 'seconds': payload['seconds']}), flush=True)


if __name__ == '__main__':
    main()
