"""Verified cleanup handoff to the existing TU/ROM integration gate."""
import hashlib
from pathlib import Path
import sqlite3
import time
from eval.agentrepair import _atomic_json
from solver import readability, residual, workspace


def clean(repo, db, function, node, output, max_attempts=12):
    source = Path(node['source']).read_text()
    if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
        raise ValueError('cleanup source changed outside controller')
    ws = workspace.bootstrap(repo, function)
    accepted = {}
    with sqlite3.connect(db, timeout=120) as conn:
        def evaluate(code, parent):
            tag = f'{function}_cleanup_{time.time_ns()}'
            att = workspace.score(ws, repo, tag, code, conn=conn, func=function,
                parent_attempt_id=parent if parent is not None else node['attempt_id'],
                strategy='campaign-readable-exact', relation='verified-cleanup',
                action='readability proposal; exactness and frontend preserved')
            accepted[hashlib.sha256(code.encode()).hexdigest()] = (att, ws / (tag + '.o'))
            return att
        report = readability.clean(source, function, evaluate, max_attempts=max_attempts,
                                    checkpoint=lambda row: _atomic_json(output, row))
    if report.get('accepted', 0):
        code = report['best_source']
        att, obj = accepted[hashlib.sha256(code.encode()).hexdigest()]
        path = output.with_suffix('.best.c')
        path.write_text(code, encoding='utf-8')
        node.update(source=str(path), source_sha256=hashlib.sha256(code.encode()).hexdigest(),
                    attempt_id=att.receipt_id, verification=att.verification, score=att.score,
                    residual=residual.build(att, target_asm=workspace.target_asm(ws, function),
                        target_object=ws / 'target.o', candidate_object=obj).to_dict())
        node['semantic_validation'] = None  # previous source-bound samples are stale
    node['cleanup'] = {'receipt': str(output), 'source_sha256': node['source_sha256'],
                       'status': report['status'], 'accepted': report.get('accepted', 0)}
    return report
