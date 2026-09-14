"""Mine current source-bound instruction residuals, not repeated attempt history.

Read-only diagnostics. Frequency, textual displacement and short hunk signatures
are candidate explanations, never source edits or semantic equivalence evidence.
"""
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from eval import campaign_state
from solver import cfg, workspace

EXACT = {'object_exact', 'integrated'}
REGISTER = re.compile(r'(?<![\w])\$?(?:f\d+|zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b|\$\d+\b')


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def implementation():
    return {name:digest(Path(path).read_bytes()) for name,path in {
        'eval/residual_patterns.py':__file__, 'eval/campaign_state.py':campaign_state.__file__,
        'solver/cfg.py':cfg.__file__, 'solver/workspace.py':workspace.__file__}.items()}


def opcode(text):
    match = re.match(r'^([a-z][a-z0-9.]*)(?:\s|$)', text)
    return match[1] if match else None


def changes(diff):
    """Contiguous change blocks; never join across context or hunk boundaries."""
    blocks, current = [], {'target': [], 'candidate': []}
    ignored = 0
    for line in diff.splitlines()+['']:
        if line.startswith(('---', '+++')):
            continue
        if line[:1] in ('-', '+'):
            body = re.sub(r'\s+', ' ', line[1:].strip())
            if opcode(body):
                current['target' if line[0]=='-' else 'candidate'].append(body)
            else:
                ignored += 1
        else:
            if any(current.values()):
                blocks.append(current)
                current = {'target': [], 'candidate': []}
    return blocks, ignored


def signature(target, candidate):
    """Alpha-rename registers jointly, retaining reuse, literals and ABI anchors."""
    names = {}
    def rename(match):
        name = match[0].lstrip('$')
        if name in {'zero', 'sp', 'ra'}:
            return name
        return names.setdefault(name, ('F' if re.fullmatch(r'f\d+', name) else 'R')+str(len(names)))
    return ' ; '.join(REGISTER.sub(rename, x) for x in target)+' => '+' ; '.join(REGISTER.sub(rename, x) for x in candidate)


def pair_family(target, candidate):
    """Describe a one-instruction block without inferring a C cause."""
    if target == candidate:
        return 'textually_identical'
    left, right = opcode(target), opcode(candidate)
    memory = re.compile(r'^(\w+)\s+([^,]+),\s*([^()]+)\(([^()]+)\)$')
    a,b = memory.fullmatch(target),memory.fullmatch(candidate)
    if a and b and left==right and a[2]==b[2] and a[4]==b[4] and a[3]!=b[3]:
        return 'stack_slot_offset' if a[4].lstrip('$')=='sp' else 'memory_offset'
    if left==right and re.fullmatch(r'addiu\s+\$?sp,\$?sp,.*',target) and re.fullmatch(r'addiu\s+\$?sp,\$?sp,.*',candidate):
        return 'stack_frame_adjustment'
    if left==right and REGISTER.sub('REG',target)==REGISTER.sub('REG',candidate):
        return 'register_operands'
    if {left,right} in ({'lb','lbu'},{'lh','lhu'}) and target.split(None,1)[1]==candidate.split(None,1)[1]:
        return 'load_signedness_opcode'
    if left==right and (cfg.has_delay_slot(left) or cfg.is_conditional_branch(left)):
        return 'control_operands'
    if left==right and ('%hi(' in target+candidate or '%lo(' in target+candidate):
        return 'relocation_expression'
    if left==right:
        return 'other_same_opcode_operands'
    return 'opcode_substitution'


def analyse(records):
    """Each record must represent a distinct checkpoint-selected function."""
    seen = set()
    groups = {key: defaultdict(lambda: {'occurrences':0, 'names':set(), 'target_bytes':0, 'examples':[]})
              for key in ('target_opcodes','candidate_opcodes','target_ngrams','candidate_ngrams',
                          'replacement_signatures','single_instruction_families','textually_displaced')}
    baseline = Counter()
    baseline_functions = Counter()
    totals = Counter()
    per_function = []
    for record in records:
        name = record['name']
        if name in seen:
            raise ValueError('multiple selected candidates for '+name)
        seen.add(name)
        blocks, ignored = changes(record['diff'])
        totals['ignored_noninstruction_lines'] += ignored
        totals['functions'] += 1
        base = Counter(record.get('target_opcodes', []))
        baseline.update(base)
        baseline_functions.update(base.keys())
        totals['baseline_functions'] += bool(base)
        totals['baseline_instructions'] += sum(base.values())
        local = {key:Counter() for key in groups}
        removed, added = [], []
        for block in blocks:
            for side in ('target','candidate'):
                ops = [opcode(x) for x in block[side]]
                local[side+'_opcodes'].update(ops)
                for n in (2,3):
                    local[side+'_ngrams'].update(' '.join(ops[i:i+n]) for i in range(len(ops)-n+1))
            removed.extend(block['target'])
            added.extend(block['candidate'])
            # Full short replacement blocks only; no positional zip of unrelated
            # instructions, and no claim that this is a control-flow alignment.
            if 0 < len(block['target']) <= 4 and 0 < len(block['candidate']) <= 4:
                local['replacement_signatures'][signature(block['target'],block['candidate'])] += 1
            if len(block['target']) == len(block['candidate']) == 1:
                local['single_instruction_families'][pair_family(block['target'][0],block['candidate'][0])] += 1
        common = Counter(removed) & Counter(added)
        for instruction, count in common.items():
            local['textually_displaced'][opcode(instruction)] += count
        totals['removed_instructions'] += len(removed)
        totals['added_instructions'] += len(added)
        totals['textually_displaced_pairs'] += sum(common.values())
        for group, counts in local.items():
            for key, count in counts.items():
                bucket = groups[group][key]
                bucket['occurrences'] += count
                bucket['names'].add(name)
                bucket['target_bytes'] += record.get('size') or 0
                if len(bucket['examples']) < 5:
                    bucket['examples'].append({'name':name,'attempt_id':record['attempt_id'],
                        'source_sha256':record['source_sha256'],'diff_sha256':digest(record['diff'])})
        per_function.append({k:record.get(k) for k in ('name','attempt_id','size','status','semantic_status','source_sha256')} |
            {'diff_sha256':digest(record['diff']), 'removed':len(removed),'added':len(added),
             'textually_displaced_pairs':sum(common.values())})
    output = {}
    for group, buckets in groups.items():
        rows = []
        for key, bucket in buckets.items():
            row = {'pattern':key, 'functions':len(bucket['names']),
                   **{k:v for k,v in bucket.items() if k!='names'}}
            if group == 'target_opcodes':
                row['baseline_occurrences'] = baseline[key]
                row['baseline_functions'] = baseline_functions[key]
                # Different assembler pseudo-op dialects and missing baseline
                # files make this an annotation, not an automatic enrichment score.
            rows.append(row)
        output[group] = sorted(rows,key=lambda r:(-r['functions'],-r['occurrences'],r['pattern']))
    return {'version':1,'scope':'One selected compiled nonexact candidate per checkpoint function. '
            'Textual residual patterns are hypotheses; no source/callee behavior inferred. '
            'Byte totals overlap across patterns. Baseline opcode dialect may differ from object diffs.',
            'totals':dict(totals),'patterns':output,'functions':per_function}


def collect(run, out):
    """Freeze the pointer, fetch only selected immutable rows, release each read."""
    run, out = Path(run).resolve(), Path(out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    pointer = json.loads((run/'campaign.json').read_bytes())
    pointer['store'] = str(run/pointer['store'])
    campaign_state.atomic(out/'checkpoint.json',pointer)
    state = campaign_state.read(out/'checkpoint.json')
    selected, excluded = {}, Counter()
    for name,node in sorted(state['nodes'].items()):
        if node.get('status') in EXACT:
            excluded['exact'] += 1
        elif not (node.get('residual') or {}).get('compiled'):
            excluded['not_compiled'] += 1
        elif not node.get('attempt_id'):
            excluded['missing_attempt_id'] += 1
        else:
            if node['attempt_id'] in selected:
                raise ValueError('checkpoint shares a selected attempt across function names')
            selected[node['attempt_id']] = (name,node)
    records, unavailable = [], []
    ids = sorted(selected)
    for start in range(0,len(ids),64):
        batch = ids[start:start+64]
        with closing(sqlite3.connect(f'file:{run/"campaign.sqlite"}?mode=ro',uri=True,timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            rows = list(conn.execute('SELECT id,func_addr,source_code,source_sha256,compiled,exact,diff_summary '
                'FROM attempts WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
        found = {row['id'] for row in rows}
        for missing in set(batch)-found:
            unavailable.append({'name':selected[missing][0],'reason':'missing attempt row'})
        for row in rows:
            name,node = selected[row['id']]
            if (row['func_addr'] != node['address'] or row['source_sha256'] != node['source_sha256']
                    or digest(row['source_code']) != node['source_sha256']):
                unavailable.append({'name':name,'reason':'source/address binding mismatch'})
                continue
            if not row['compiled'] or row['exact'] or not row['diff_summary']:
                unavailable.append({'name':name,'reason':'attempt has no compiled nonexact instruction diff'})
                continue
            path = Path(state['config']['repo'])/'nonmatchings'/name/'target.s'
            base, base_status = [], 'not pinned or missing'
            if path.is_file() and state['pins'].get(str(path)) == digest(path.read_bytes()):
                base = [i.opcode for i in cfg.parse_assembly(workspace.target_asm(path.parent,name))[0]]
                base_status = 'pinned target assembly'
            records.append({'name':name,'attempt_id':row['id'],'source_sha256':row['source_sha256'],
                'size':node.get('size'),'status':node['status'],
                'semantic_status':(node.get('semantic_validation') or {}).get('status'),
                'diff':row['diff_summary'],'target_opcodes':base,'baseline_status':base_status})
    campaign_state.atomic(out/'records.json',records)
    report = analyse(records)
    report.update(checkpoint=pointer['commit'], exclusions=dict(excluded),unavailable=unavailable,
                  implementation_sha256=implementation(),
                  auditor_sha256=digest(Path(__file__).read_bytes()),records_sha256=digest((out/'records.json').read_bytes()))
    write_report(report,out)
    return report


def write_report(report,out):
    out=Path(out)
    campaign_state.atomic(out/'report.json',report)
    lines = [f'# Current residual patterns: checkpoint {report["checkpoint"]}', '',report['scope'],'',
             f'Analysed {report["totals"]["functions"]} functions; {len(report["unavailable"])} selected records unavailable.', '']
    for group in ('target_opcodes','candidate_opcodes','single_instruction_families','replacement_signatures','target_ngrams','textually_displaced'):
        lines += ['## '+group.replace('_',' '),'','| Pattern | Functions | Occurrences | Target bytes represented |',
                  '|---|---:|---:|---:|']
        for row in report['patterns'][group][:20]:
            lines.append(f'| `{row["pattern"]}` | {row["functions"]} | {row["occurrences"]} | {row["target_bytes"]} |')
        lines += ['']
    (out/'README.md').write_text('\n'.join(lines),encoding='utf-8')


def replay(records_path,out):
    """Reanalyse retained input with no campaign/attempt database access."""
    records_path,out=Path(records_path).resolve(),Path(out).resolve()
    raw=records_path.read_bytes()
    prior=json.loads(records_path.with_name('report.json').read_bytes())
    if digest(raw)!=prior['records_sha256']:
        raise ValueError('saved audit records changed')
    report=analyse(json.loads(raw))
    report.update({k:prior[k] for k in ('checkpoint','exclusions','unavailable','records_sha256')})
    report.update(auditor_sha256=digest(Path(__file__).read_bytes()),implementation_sha256=implementation(),source_records=str(records_path))
    out.mkdir(parents=True,exist_ok=False)
    (out/'records.json').write_bytes(raw)
    write_report(report,out)
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--run',type=Path)
    source.add_argument('--records',type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args = parser.parse_args()
    report = collect(args.run,args.out) if args.run else replay(args.records,args.out)
    print(json.dumps({'checkpoint':report['checkpoint'],'totals':report['totals'],
                      'exclusions':report['exclusions'],'unavailable':len(report['unavailable'])}))


if __name__=='__main__':main()
