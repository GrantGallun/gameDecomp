import sqlite3
from collections import Counter, defaultdict

c = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)
attempted = {r[0] for r in c.execute('select distinct func_addr from attempts')}
compiled_any = {r[0] for r in c.execute('select distinct func_addr from attempts where compiled=1')}
never = attempted - compiled_any
names = {a: n for a, n in c.execute('select addr, name from functions')}


def kinds(err):
    """Every failure kind present in one stderr blob. Unambiguous -- no 'dominant' tie-breaks."""
    e = err or ''
    out = set()
    if 'do-while' in e:
        out.add('do_while_ban')
    if 'no text symbols' in e:
        out.add('no_text_symbols')
    if 'Syntax Error' in e:
        out.add('syntax')
    if 'undefined' in e and "'" in e:
        out.add('undefined_ident')
    if 'Selector requires' in e:
        out.add('selector')
    if 'redeclaration' in e:
        out.add('redeclaration')
    if not out:
        out.add('other')
    return out


per = {}
for addr, in c.execute('select distinct func_addr from attempts'):
    if addr not in never:
        continue
    errs = [e for (e,) in c.execute(
        'select compiler_stderr from attempts where func_addr=? and compiled=0', (addr,))]
    per[addr] = [kinds(e) for e in errs]

has = Counter()
for addr, ks in per.items():
    for k in set().union(*ks) if ks else set():
        has[k] += 1
print(f'=== of the {len(per)} never-compiled functions, how many show each failure kind ===')
for k, v in has.most_common():
    print(f'   {v:>4}  {k}')

only = {k: [a for a, ks in per.items() if all(k in s for s in ks)]
        for k in ('do_while_ban', 'no_text_symbols', 'syntax')}
print()
print('=== functions whose EVERY failed attempt shows only that kind (cleanly fixable) ===')
for k, v in only.items():
    print(f'   {len(v):>4}  only {k}')
print()
print('   do-while-only functions:',
      ', '.join(names.get(a, str(a)) for a in list(only['do_while_ban'])[:14]))
print()
print('   no-text-symbols-only functions:',
      ', '.join(names.get(a, str(a)) for a in list(only['no_text_symbols'])[:14]))

print()
print('=== attempt volume per clean class ===')
for k, v in only.items():
    n = sum(len(per[a]) for a in v)
    print(f'   {k:<20} {len(v):>4} funcs  {n:>5} attempts')

print()
print('=== sanity: totals ===')
print('   never-compiled funcs:', len(per))
print('   total attempts in them:', sum(len(v) for v in per.values()))
unclass = [a for a, ks in per.items() if all(s == {'other'} for s in ks)]
print('   functions with ONLY unclassified errors:', len(unclass))
for a in unclass[:5]:
    e = c.execute('select compiler_stderr from attempts where func_addr=? and compiled=0 limit 1',
                  (a,)).fetchone()[0]
    print('     ', names.get(a, str(a)), '->', (e or '').strip().splitlines()[0][:120])
