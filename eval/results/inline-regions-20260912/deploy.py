"""Install only tested scoped inlining changes while campaign is paused/drained."""
import importlib.util
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(OUT/'staged-code'))
spec=importlib.util.spec_from_file_location('inline_deploy',OUT/'deploy_protocol.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.OUT=OUT
module.ROOT=ROOT
module.RUN=ROOT/'eval/results/resume-pipeline-20260908'
module.STAGE=OUT/'staged-code'
module.REVISION='20260912-inline-regions'
module.AMENDMENT_KIND='inline-regions-amendment'
module.main()
