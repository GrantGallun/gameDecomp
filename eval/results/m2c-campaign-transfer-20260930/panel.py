"""Freeze 64 new functions by binary features before any draft outcomes.

This is out of sample for adapter development, not a claim of project-wide
unseen functions: the binary-context policy has its own historical fit pool.
Sealed heldout names and the original sample's entire TUs remain excluded.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(ROOT))
from solver import cfg,compiler_recipe


def sha(data): return hashlib.sha256(data).hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+'\n')
def names(value):
    return {x if isinstance(x,str) else x.get('function') for x in value if isinstance(x,(str,dict))}-{None}
def size_group(count):
    return 0 if count<=32 else 1 if count<=96 else 2 if count<=256 else 3


def freeze(repo,output):
    output.mkdir(parents=True,exist_ok=False)
    census=json.loads((ROOT/'eval/results/joint-reconstruction-20260930/census.json').read_text())
    old=json.loads((ROOT/'eval/results/m2c-campaign-connect-20260930/portable/selection.json').read_text())['functions']
    excluded_tus={census['metadata'][fn]['tu_id'] for fn in old}
    dev,sealed=set(),set(census['heldout'])
    for path in (ROOT/'eval/sets').glob('*.json'):
        data=json.loads(path.read_text())
        dev.update(names(data.get('dev',[])))
        if data.get('kind')=='logic-first-connected-dev-cluster': dev.update(names(data.get('cluster',[])))
        for key in ('heldout','test','validation'): sealed.update(names(data.get(key,[])))
    for path in (ROOT/'eval/results').glob('m2c-*/selection.json'):
        old+=list(names(json.loads(path.read_text()).get('functions',[])))
    old=set(old)
    for fn in old:
        if fn in census['metadata']: excluded_tus.add(census['metadata'][fn]['tu_id'])
    assembly_index=collections.defaultdict(list)
    for path in sorted((repo/'asm').rglob('*.s')):
        text=path.read_text()
        labels=re.findall(r'(?m)^\s*glabel\s+([A-Za-z_]\w*)\b',text)
        if len(labels)==1: assembly_index[labels[0]].append((path,text))
    pool=[]; declined=[]
    for fn in sorted(dev-sealed-old):
        metadata=census['metadata'].get(fn)
        if not metadata or metadata['tu_id'] in excluded_tus or not 4<=metadata['insn_count']<=800:
            continue
        found=assembly_index.get(fn,[])
        if len(found)!=1:
            declined.append({'function':fn,'reason':'missing or ambiguous single-function binary disassembly'})
            continue
        path,text=found[0]
        try:
            recipe=compiler_recipe.resolve(repo,metadata['compile_target'])
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
            declined.append({'function':fn,'reason':'unsupported compiler recipe','error':str(exc)})
            continue
        instructions=cfg.parse_assembly(text)[0]
        operations={i.opcode for i in instructions}
        address_pattern=bool(operations&{'addu','add'} and operations&{'sll','sllv'} and
                             operations&{'lb','lbu','lh','lhu','lw','sb','sh','sw'})
        pool.append({'function':fn,'size_group':size_group(metadata['insn_count']),
                     'address_pattern':address_pattern,'metadata':metadata,
                     'assembly_path':str(path),'assembly_sha256':sha(text.encode()),
                     'calls':bool(operations&{'jal','jalr'}),
                     'floating_point':bool(operations&{'lwc1','swc1','ldc1','sdc1','mtc1','mfc1'}),
                     'rank':sha(('m2c-transfer-20260930-v1:'+fn).encode()),'recipe':recipe})
    write(output/'selection-pool.json',{'functions':pool,'declined':declined,
        'by_size_group':dict(collections.Counter(r['size_group'] for r in pool)),
        'address_by_size_group':dict(collections.Counter(r['size_group'] for r in pool if r['address_pattern']))})
    # Group quotas and TU cap are fixed before generation. Broad selection runs
    # first; enriched selection cannot replace a broad outcome.
    chosen=[];tu_counts=collections.Counter()
    for label,enriched in [('broad',False),('address-pattern',True)]:
        for bucket in range(4):
            eligible=sorted((r for r in pool if r['size_group']==bucket and
                (not enriched or r['address_pattern'])),key=lambda r:r['rank'])
            picked=0
            for item in eligible:
                if any(r['function']==item['function'] for r in chosen) or tu_counts[item['metadata']['tu_id']]>=2: continue
                chosen.append({**item,'group':label})
                # Membership is checked by function because the group tag was added.
                tu_counts[item['metadata']['tu_id']]+=1
                picked+=1
                if picked==8: break
        # Some enriched strata are small. Fill any shortfall from remaining
        # binary-eligible functions, preferring the least represented size bin.
        while sum(r['group']==label for r in chosen)<32:
            counts=collections.Counter(r['size_group'] for r in chosen if r['group']==label)
            remaining=[r for r in pool if (not enriched or r['address_pattern']) and
                not any(c['function']==r['function'] for c in chosen) and tu_counts[r['metadata']['tu_id']]<2]
            if not remaining: raise RuntimeError('insufficient independent binary pool for '+label)
            item=min(remaining,key=lambda r:(counts[r['size_group']],r['rank']))
            chosen.append({**item,'group':label});tu_counts[item['metadata']['tu_id']]+=1
    assert len({r['function'] for r in chosen})==64
    assert not {r['function'] for r in chosen}&sealed
    assert not {r['metadata']['tu_id'] for r in chosen}&excluded_tus
    selection={'functions':[r['function'] for r in chosen],
               'groups':{r['function']:r['group'] for r in chosen},
               'metadata':{r['function']:r['metadata'] for r in chosen},
               'binary_features':chosen,'excluded_functions':sorted(old),'excluded_tus':sorted(excluded_tus),
               'heldout_overlap':[],'heldout_names_sha256':sha(json.dumps(sorted(sealed)).encode()),
               'eligible_pool_count':len(pool),'selection_declines':declined,
               'policy':'32 broad + 32 shifted-address-pattern; target 8 per size stratum, fill binary-only shortfalls from least represented eligible bins; hash rank; <=2 per TU; no outcome replacements',
               'training_eligible':False,'adapter_sha256':sha((ROOT/'eval/results/m2c-reconstruction-20260930/byte_address.py').read_bytes()),
               'panel_sha256':sha(Path(__file__).read_bytes())}
    write(output/'selection.json',selection)
    # Selection is sealed before target assembly, m2c generation or C compilation.
    prelude=(repo/'tools/claude-decomp-env/prelude.inc').read_text()+'\n'+(repo/'include/macro.inc').read_text()+'\n'
    for item in chosen:
        fn=item['function'];folder=output/'targets'/fn;folder.mkdir(parents=True)
        original=Path(item['assembly_path']).read_text()
        assert sha(original.encode())==item['assembly_sha256']
        target=folder/'target.s';target.write_text(prelude+original)
        command=['mips-linux-gnu-as','-EB','-march=vr4300','-mtune=vr4300','-Iinclude','-o',str(folder/'target.o'),str(target)]
        process=subprocess.run(command,cwd=repo,capture_output=True,text=True,timeout=60)
        write(folder/'target-receipt.json',{'command':command,'returncode':process.returncode,
              'source_path':item['assembly_path'],'original_sha256':item['assembly_sha256'],
              'stderr':process.stderr,'target_sha256':sha((folder/'target.o').read_bytes()) if (folder/'target.o').exists() else None})
        if process.returncode: raise RuntimeError('frozen target assembly failed: '+fn+': '+process.stderr)
        # The frozen connection runner consumes target identities from these
        # input receipts. They are not compiler attempts or observed outcomes.
        write(output/'compiles'/fn/'baseline/attempt-00001/receipt.json',
              {'compile_target':item['metadata']['compile_target'],'kind':'target-identity-only-no-candidate-compile'})
    print(json.dumps({'frozen_functions':64,'translation_units':len(tu_counts),
                      'groups':dict(collections.Counter(r['group'] for r in chosen))}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();freeze(args.repo,args.output)
