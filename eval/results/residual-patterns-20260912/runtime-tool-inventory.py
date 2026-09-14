"""Read-only local tool inventory; never contacts or controls a debugger."""
import importlib.util,json,shutil,subprocess
from pathlib import Path
names=('mupen64plus','simple64','ares','cen64','retroarch','gdb','gdb-multiarch','z3')
processes=subprocess.run(['ps','-eo','pid,comm,args'],capture_output=True,text=True,check=True).stdout.splitlines()
report={'path_tools':{name:shutil.which(name) for name in names},
 'python_modules':{name:bool(importlib.util.find_spec(name)) for name in ('z3','angr','unicorn')},
 'emulator_debugger_processes':[line for line in processes if any(name in line.split(maxsplit=2)[1].lower() for name in names)],
 'scope':'PATH and current process names only; absence does not prove no installation elsewhere'}
out=Path(__file__).with_name('runtime-tool-inventory.json')
out.write_text(json.dumps(report,indent=2));print(json.dumps(report))
