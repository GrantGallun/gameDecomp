"""Audit existing compiler worlds against the machinery's intended contracts.

Run in the native workspace containing the recorded target objects. This command
does not generate C, probe layouts, compile candidates, or mutate the attempt DB.
"""
import argparse
from collections import Counter
import hashlib
import ast
import json
from pathlib import Path
import sqlite3
import shutil
import sys

from eval.repair_graph import build_graph
from eval.search_replay import digest, load_world
from solver.capability_contracts import catalog
from solver.capability_map import compare, inspect_expectations, validate_assessment, workspace_assessment
from solver.repair_theory import validate_map


def write(path, value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def analyze(report_path, output, *, connected='theory'):
    report_path,output=Path(report_path),Path(output)
    report=json.loads(report_path.read_text())
    if not report.get('complete'):
        raise ValueError('capability audit requires completed compiler report')
    # Check original run identities before interpreting any verdict.
    for path,sha in report['artifacts'].items():
        if hashlib.sha256((report_path.parent.parent/path).read_bytes()).hexdigest()!=sha:
            raise ValueError('original compiler artifact changed: '+path)
    for path,sha in report['fixed_files'].items():
        if hashlib.sha256((Path(report['code_root'])/path).read_bytes()).hexdigest()!=sha:
            raise ValueError('original compiler code changed: '+path)
    contracts=catalog()
    identity_path=Path(report['code_root'])/'eval/results/dream-search-20260922/pilot.py'
    # Load only the frozen read-only identity function, not its driver's module
    # setup (which changes sys.path and imports unrelated campaign machinery).
    tree=ast.parse(identity_path.read_text())
    definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compiler_identity')
    identity={'Path':Path,'hashlib':hashlib,'shutil':shutil,'sys':sys,'digest':digest}
    exec(compile(ast.Module(body=[definition],type_ignores=[]),str(identity_path),'exec'),identity)
    compiler_identity=identity['compiler_identity']
    db=sqlite3.connect(f"file:{report['database']}?mode=ro",uri=True)
    db.row_factory=sqlite3.Row
    attempts={r['id']:dict(r) for r in db.execute('SELECT * FROM attempts')}
    output.mkdir(parents=True,exist_ok=False)
    (output/'worlds').mkdir()
    runs=[]; used=set(); source_worlds={}; owner_ids={c['id'] for c in contracts['contracts']}
    status_counts=Counter()
    for row in report['runs']:
        world_path=report_path.parent/row['world']
        world=load_world(world_path)
        build_graph([world])
        source_worlds[row['world']]=hashlib.sha256(world_path.read_bytes()).hexdigest()
        repo=Path(report['database']).parent/row['function']/row['arm']
        ws=repo/'nonmatchings'/row['function']
        if compiler_identity(repo,ws)!=world['context']['compiler_sha256']:
            raise ValueError('workspace compiler/header/assembly identity changed')
        assessments={}; transitions=[];conflicts=[]
        for node in world['nodes']:
            v=node['verdict'];stored=attempts[v['receipt_id']]
            if (stored['source_code']!=node['source'] or stored['source_sha256']!=node['source_sha256']
                    or stored['parent_attempt_id']!=node['parent_receipt_id']
                    or bool(stored['exact'])!=v['exact'] or bool(stored['compiled'])!=v['compiled']):
                raise ValueError('attempt receipt differs from recorded world')
            metadata=json.loads(stored['sampling'])
            for key in ('verification','frontend','compiler_recipe','source_attribution'):
                if metadata.get(key)!=v.get(key):raise ValueError('attempt evidence differs from recorded world')
            if v['receipt_id'] in used:raise ValueError('duplicate attempt across worlds')
            used.add(v['receipt_id'])
            a=workspace_assessment(node['source'],row['function'],v,context=world['context'],
                repo=repo,workspace=ws,contracts=contracts,connected=connected)
            validate_assessment(a)
            assessments[node['id']]=a
            # A scope-labelled static assessment is retained for every observation,
            # including failed intermediates. It never changes the original world.
            family=node['family']
            if family in {'register_storage','address_reuse','parameter_reuse'}:family='storage_search'
            if node['parent'] is not None and family in owner_ids:
                transitions.append(compare(assessments[node['parent']],a,contract_id=family))
        for parent,mapping in row.get('theory',{}).get('maps',{}).items():
            validate_map(mapping)
            a=assessments[parent]
            if mapping['inputs']['source']!=a['inputs']['source'] or mapping['inputs']['verdict']!=a['inputs']['verdict']:
                raise ValueError('retained proposal observation differs from world')
            for route in mapping['routes']:
                if route.get('evidence',{}).get('assembly_sha256')!=a['assembly_sha256']:
                    raise ValueError('retained route assembly binding differs')
            conflicts.extend({'parent':parent,**c,'scope':'prior proposal observation against current intended catalogue'}
                             for c in inspect_expectations(a,mapping['routes']))
        if compiler_identity(repo,ws)!=world['context']['compiler_sha256']:
            raise ValueError('workspace evidence changed during assessment')
        file=row['world'].replace('.world.json','.capability.json')
        write(output/'worlds'/file,{'original_world_sha256':digest(world),'assessments':assessments,
                                  'transitions':transitions,'expectation_conflicts':conflicts})
        best_id=row.get('theory',{}).get('best_intake_id') or row['best_id']
        best=assessments[best_id]
        needed=[c for c in best['capabilities'] if c['needed']]
        status_counts.update(c['status'] for c in needed)
        runs.append({'function':row['function'],'arm':row['arm'],'phase':row['phase'],
            'exact':row['exact'],'observations':len(assessments),'best_id':best_id,
            'best_receipt_id':best['receipt_id'],'artifact':'worlds/'+file,
            'root_requirements':assessments['root']['requirements'],
            'requirements':best['requirements'],'goals':best['goals'],'composition':best['composition'],
            'capabilities':needed,'investigations':best['investigations'],'transitions':transitions,
            'expectation_conflicts':conflicts,'route_observations_available':'theory' in row})
    result={'schema_version':1,'kind':'retrospective-machinery-capability-audit','complete':True,
        'new_compiler_calls':0,'observations_analyzed':len(used),'worlds_analyzed':len(runs),
        'original_report':str(report_path),'original_report_sha256':digest(report),
        'world_file_sha256':source_worlds,'catalog_sha256':contracts['sha256'],'caller':connected,
        'status_counts':dict(status_counts),'runs':runs,'training_eligible':False,
        'scope':'intended contracts and observations; not a measured numerical ceiling on attainable matches'}
    result['sha256']=digest(result)
    write(output/'catalog.json',contracts)
    write(output/'report.json',result)
    (output/'MAP.md').write_text(markdown(result),encoding='utf-8')
    return result


def markdown(report):
    lines=['# Capability ceiling of the current machinery','',
        'This map separates intended component behavior from observed successes. '
        'A correct candidate generator can still produce nonmatching C; a correct '
        'checker does not supply a complete synthesis algorithm.','',
        f"Analyzed {report['observations_analyzed']} existing compiler observations across "
        f"{report['worlds_analyzed']} worlds. New compiler calls: **0**.",
        'Contracts cite hashed owner, test and wiring files in [catalog.json](catalog.json). '
        'Full source-bound assessments are linked from [report.json](report.json).','',
        '```mermaid','flowchart LR',
        '  B[Target binary and assembly] --> R[Required operations and interfaces]',
        '  C[Declared component contracts] --> P[Check domains and prerequisites]',
        '  R --> P','  P --> W[Check caller wiring]',
        '  W --> H[Candidate construction expectations]',
        '  H --> O[Compare with observed attempts]',
        '  O --> D[Investigate implementation, wiring, representation or missing evidence]',
        '  H -. composition and complete search remain unproved .-> X[Exact C reachability]',
        '  V[Actual source-bound exact certificate] --> X','```','',
        '| Function | Exact C witness | Candidate requirements | Remaining composition obligations |',
        '|---|---|---|---|']
    for row in report['runs']:
        if row['arm']!='theory':continue
        req=row['requirements']
        lines.append(f"| `{row['function']}` | {'Receipt '+str(row['best_receipt_id']) if row['exact'] else 'Not yet'} | "
            f"{', '.join(req['needs'])} | {', '.join(row['composition']['unresolved']) or 'Concrete exact witness retained'} |")
    for row in report['runs']:
        if row['arm']!='theory' or row['exact']:continue
        lines+=['',f"## {row['function']}",'',
                f"[Assessment]({row['artifact']}); best intake receipt {row['best_receipt_id']}.",'',
                '| Component | Intended output | Applicability | Connected here | Unmet or unknown conditions |',
                '|---|---|---|---|---|']
        for c in row['capabilities']:
            conditions=c['failed_domain']+c['missing_prerequisites']+c['unknown_prerequisites']
            lines.append(f"| `{c['id']}` | {c['output']} | {c['applicability']} | {'Yes' if c['connected'] else 'No; '+', '.join(c['callers'])} | {', '.join(conditions) or 'None in this declared contract'} |")
    lines+=['','## Meaning of the ceiling','',
        'The current architecture includes candidate constructors and finite search, '
        'not a complete inverse compiler. Therefore component coverage alone cannot '
        'establish an exact-match percentage achievable under perfect implementation. '
        'Known exact candidates are concrete witnesses. Other targets remain open '
        'with explicit local expectations and missing composition requirements.','',
        'Unlisted mechanisms and unclassified instructions remain unassessed. '
        'An unavailable route in this caller does not mean the repository lacks it. '
        'All statuses refer to the catalogue\'s declared domains; they do not assert '
        'that a target is globally impossible.','',
        'The original binary is an available fallback when its target object is present. '
        'Keeping it does not count as C reconstruction. Bounded semantic checks do not '
        'prove all-input equivalence. These artifacts remain training-ineligible.','']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--caller',choices=['theory','compile-recovery','model-repair'],default='theory')
    args=parser.parse_args()
    result=analyze(args.report,args.output,connected=args.caller)
    print(json.dumps({k:result[k] for k in ('complete','new_compiler_calls','observations_analyzed','worlds_analyzed','status_counts')},indent=2))


if __name__=='__main__':
    main()
