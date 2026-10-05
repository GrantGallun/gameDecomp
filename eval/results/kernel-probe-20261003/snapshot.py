"""Read-only source evidence for kernel eligibility; does not import CUDA."""
import ast
import importlib.metadata
import json
from pathlib import Path

root = Path(__file__).resolve().parent
site = Path('/home/grant/decomp/serve-venv/lib/python3.12/site-packages/vllm')
selections = {
    'model_executor/kernels/linear/scaled_mm/b12x.py': ['B12xTensorFP8ScaledMMLinearKernel'],
    'model_executor/kernels/linear/scaled_mm/flashinfer.py': ['FlashInferFP8ScaledMMLinearKernel'],
    'model_executor/kernels/linear/scaled_mm/pytorch.py': ['ChannelWiseTorchFP8ScaledMMLinearKernel'],
    'model_executor/layers/quantization/online/fp8.py': ['Fp8PerTensorOnlineLinearMethod'],
}
evidence = {'vllm_version': importlib.metadata.version('vllm'), 'source_excerpts': {}}
try:
    evidence['b12x_version'] = importlib.metadata.version('b12x')
except importlib.metadata.PackageNotFoundError:
    evidence['b12x_version'] = None
for relative, names in selections.items():
    source = (site / relative).read_text()
    tree = ast.parse(source)
    excerpts = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in names:
            excerpts[node.name] = {
                method.name: ast.get_source_segment(source, method)
                for method in node.body if isinstance(method, ast.FunctionDef)
                and method.name in ['__init__', 'is_supported', 'can_implement', 'apply_scaled_mm']}
    evidence['source_excerpts'][relative] = excerpts
model = Path('/home/grant/decomp/models/qwen2.5-coder-7b')
evidence['checkpoint_manifest'] = [{'name': p.name, 'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
                                   for p in sorted(model.glob('*.safetensors'))]
(root / 'kernel_source_evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
print('Recorded kernel constraints and checkpoint manifest.')
