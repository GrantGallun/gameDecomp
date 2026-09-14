"""Budgeted exploration of stalled deterministic repair trees (opt-in).

Reuse residual-driven rewrites; change allocation of attempts, not correctness.
The best candidate is separate from the exploratory pool. A lower score is never
an exactness verdict. No new model calls or reference-source access are involved.
"""
from collections import Counter, deque
from dataclasses import dataclass
import re

from solver import project_headers, repair, workspace


@dataclass(frozen=True)
class Config:
    patience: int = 4
    pool_size: int = 12
    quantum: int = 2
    lookahead: int = 2

    def __post_init__(self):
        if min(self.patience, self.pool_size, self.quantum, self.lookahead) < 1:
            raise ValueError('plateau limits must be positive')


def identity(state):
    """Token shape plus residual identity; formatting is not diversity."""
    masked = project_headers._mask_noncode(state.source)
    tokens = re.findall(r'[A-Za-z_]\w*|0x[\da-fA-F]+|\d+|[^\s]', masked)
    tokens = ['LOCAL' if re.fullmatch(r'(?:temp|var)_[A-Za-z0-9_]+', t) else t
              for t in tokens]
    return repair._digest(' '.join(tokens)), repair._digest(state.attempt.diff)


def verified(state):
    att, cert = state.attempt, state.attempt.verification or {}
    return bool(att.compiled and att.exact and
        (att.frontend or {}).get('passed') is True and
        cert.get('kind') == 'mips_object_section_certificate' and
        cert.get('exact') is True and
        cert.get('candidate_source_sha256') == repair._digest(state.source))


def search(base, evaluate, *, budget, max_depth, config=None):
    """Return best state and audit log. evaluate(source, parent, rewrite) logs it.

    A quantum prevents shallow siblings consuming the entire budget. On a stall,
    underexplored residual/source shapes get bounded consecutive follow-up steps.
    Pending rewrites survive revisits; all evaluated sources are deduplicated.
    """
    config = config or Config()
    best = base
    pool = {repair._digest(base.source): base}
    pending = {}
    seen = set(pool)
    visits = Counter()
    shape_visits = Counter()
    stalled = tried = episodes = 0
    follow = None
    grace = 0
    log = []

    def tasks(state):
        key = repair._digest(state.source)
        if key not in pending:
            # Interleave operator families, not a long alphabetical family block.
            groups = {}
            for _, rw in repair._proposal_tasks([state]):
                groups.setdefault(rw.kind, deque()).append(rw)
            queue = deque()
            while any(groups.values()):
                for group in groups.values():
                    if group:
                        queue.append(group.popleft())
            pending[key] = queue
        return pending[key]

    while tried < budget:
        eligible = [s for s in pool.values() if len(s.labels) < max_depth and tasks(s)]
        if not eligible:
            log.append('plateau: available rewrite pool exhausted (not proof of a local optimum)')
            break
        exploring = stalled >= config.patience
        if follow in pool and grace and pool[follow] in eligible:
            parent = pool[follow]
            grace -= 1
            log.append(f'lookahead: depth={len(parent.labels)} score={parent.attempt.score:.3f}')
        elif exploring:
            parent = min(eligible, key=lambda s: (shape_visits[identity(s)],
                         visits[repair._digest(s.source)], repair._rank(s)))
            grace = config.lookahead - 1
            episodes += 1
            log.append(f'plateau detected after {stalled} non-improving attempts; '
                       f'explore depth={len(parent.labels)} score={parent.attempt.score:.3f}')
            stalled = 0
        else:
            parent = min(eligible, key=lambda s: (visits[repair._digest(s.source)], repair._rank(s)))
        key = repair._digest(parent.source)
        visits[key] += 1
        shape_visits[identity(parent)] += 1
        children = []
        spent = 0
        queue = tasks(parent)
        while queue and spent < config.quantum and tried < budget:
            rw = queue.popleft()
            candidate = rw(parent.source)
            child_key = repair._digest(candidate)
            if child_key in seen:
                continue
            seen.add(child_key)
            tried += 1
            spent += 1
            att = evaluate(candidate, parent, rw)
            stalled += 1
            if not att.compiled or (att.frontend or {}).get('passed') is not True:
                continue
            child = repair._State(candidate, att, parent.labels + (rw.label,),
                                  parent.kinds + (rw.kind,))
            if verified(child):
                log.append(f'EXACT depth {len(child.labels)} after {tried} candidates: ' +
                           ' then '.join(child.labels))
                return child, log
            if att.score > best.attempt.score:
                best = child
                stalled = 0
                log.append(f'improved depth {len(child.labels)} -> {att.score:.3f}')
            pool[child_key] = child
            children.append(child)
        if children and (exploring or grace):
            next_state = min(children, key=lambda s: (shape_visits[identity(s)], repair._rank(s)))
            follow = repair._digest(next_state.source)
        else:
            follow = None
        # Bounded pool, champion pinned separately. Exhausted/depth-limited nodes
        # need no slot. Favor unseen shapes and keep a promised lookahead child.
        available = [s for s in pool.values() if len(s.labels) < max_depth and tasks(s)]
        available.sort(key=lambda s: (repair._digest(s.source) != follow,
                       shape_visits[identity(s)], visits[repair._digest(s.source)], repair._rank(s)))
        pool = {repair._digest(s.source): s for s in available[:config.pool_size]}
        pending = {k: v for k, v in pending.items() if k in pool}
    log.append(f'plateau summary: {tried} candidates, {episodes} exploration episodes, '
               f'{len(pool)} pending pool states; best={best.attempt.score:.3f}')
    return best, log
