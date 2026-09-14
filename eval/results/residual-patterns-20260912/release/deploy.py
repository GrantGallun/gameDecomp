"""Apply the tested stack-home amendment using the existing frozen-run protocol."""
import importlib.util
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'staged-code'))
spec=importlib.util.spec_from_file_location('impact_deploy',OUT.parent.parent/'impact-20260912/deploy.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.OUT=OUT
module.STAGE=OUT/'staged-code'
module.REVISION='20260912-stack-home'
module.AMENDMENT_KIND='stack-home-amendment'
module.RUNTIME_PREFIXES=('solver/','eval/','patterns/')
module.main()
