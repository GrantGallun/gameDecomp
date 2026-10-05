"""Verify measured implementation with the final focused regression set."""
import contextlib
import json
import os
import sys
from pathlib import Path
import pytest

OUT=Path(__file__).resolve().parent
sys.path.insert(0,os.environ['GAMEDECOMP_CODE_ROOT'])
tests=['global_scalar_view','header_signature_view','intake_type_views','intake_blockers',
    'intake_search','intake_runner_wiring','intake_frontend_abi','call_arity_repair',
    'frontend_fixits','frontend_full_diagnostics','project_headers_typedefs',
    'byte_pointer_offset','byte_view_order']
with (OUT/'tests-focused.log').open('w') as stream:
    with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
        code=pytest.main(['-q',*['tests/test_'+name+'.py' for name in tests]])
(OUT/'tests-focused.json').write_text(json.dumps(dict(exit_code=int(code),files=tests,
    implementation=os.environ['GAMEDECOMP_CODE_ROOT']),indent=2)+'\n')
print((OUT/'tests-focused.log').read_text()[-1000:])
raise SystemExit(int(code))
