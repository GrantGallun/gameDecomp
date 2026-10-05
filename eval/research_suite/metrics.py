"""Complete-listing metric reconstruction; never an object identity test."""
import re
from pathlib import Path

from solver import invariants
from .manifest import digest

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def reconstruct(target: str, diff: str) -> str:
    if (target and not target.endswith('\n')) or (diff and not diff.endswith('\n')) or '\\ No newline' in diff:
        raise ValueError('newline-ambiguous diff is not admitted')
    old, lines, out = target.splitlines(), diff.splitlines(), []
    cursor = index = 0
    seen = False
    while index < len(lines):
        line = lines[index]
        if line.startswith(('--- ', '+++ ')):
            index += 1
            continue
        match = HUNK.match(line)
        if not match:
            raise ValueError('unexpected or truncated diff')
        seen = True
        start, n, new_start, new_n = int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1)
        pos = start if n == 0 else start - 1
        if not cursor <= pos <= len(old):
            raise ValueError('invalid old hunk range')
        out.extend(old[cursor:pos])
        if len(out) != (new_start if new_n == 0 else new_start - 1):
            raise ValueError('invalid new hunk range')
        cursor, used_old, used_new = pos, 0, 0
        index += 1
        while index < len(lines) and not lines[index].startswith('@@'):
            line = lines[index]
            index += 1
            if line.startswith('\\ No newline'):
                continue
            if not line or line[0] not in ' +-':
                raise ValueError('invalid hunk body')
            prefix, text = line[0], line[1:]
            if prefix in ' -':
                if cursor >= len(old) or old[cursor] != text:
                    raise ValueError('target context mismatch')
                cursor += 1
                used_old += 1
            if prefix in ' +':
                out.append(text)
                used_new += 1
        if (used_old, used_new) != (n, new_n):
            raise ValueError('hunk count mismatch')
    if not seen:
        raise ValueError('empty diff lacks an independent completeness receipt')
    out.extend(old[cursor:])
    return '\n'.join(out) + ('\n' if out else '')


def measure(target: str, diff: str, *, expected_target_sha256: str | None = None) -> dict:
    sha = digest(target.encode())
    if expected_target_sha256 is not None and sha != expected_target_sha256:
        raise ValueError('historical target hash mismatch')
    candidate = reconstruct(target, diff)
    return {'target_sha256': sha, 'candidate_sha256': digest(candidate.encode()),
            'metric_sha256': digest(Path(invariants.__file__).read_bytes()),
            'historical_identity': 'pinned' if expected_target_sha256 else 'unverified',
            'hunk_vector': invariants.distance_from_diff(diff),
            'full_vector': invariants.distance(invariants.parse(target), invariants.parse(candidate)),
            'candidate': candidate, 'scope': 'normalized listing heuristic; not object equivalence'}
