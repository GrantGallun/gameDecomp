"""Build the final experiment receipt from verified raw measurements."""
import json
from pathlib import Path
import statistics

root = Path(__file__).resolve().parent
summary = json.loads((root / 'summary.json').read_text())
by_name = {r['variant']: r for r in summary}
def case(name, batch=1):
    return next(c for c in by_name[name]['cases'] if c['prompt'] == 'long' and c['concurrency'] == batch)
baseline_names = ['eager_auto', 'eager_auto_recheck']
base_tps = [case(n)['decode_tps_median'] for n in baseline_names]
graph_tps = case('graphs_only_auto')['decode_tps_median']
first = json.loads((root / 'eager_auto.json').read_text())
result = {
    'conclusion': 'CUDA graph replay improved measured decode throughput; retain CUTLASS. No production defaults changed.',
    'model': first['model'], 'gpu': 'NVIDIA GeForce RTX 5080 16 GB',
    'versions': first['versions'], 'prompts': first['prompts'],
    'method': {'generated_tokens_per_request': 512, 'greedy_sampling': True, 'ignore_eos': True,
               'measured_repeats_per_case': 3, 'warmups_excluded_per_case': 1,
               'prefix_caching': False, 'decode_tps_definition': '(output tokens - 1) / (last token time - first token time)',
               'aggregate_tps_definition': 'all output tokens / wall time including prefill',
               'baseline_repeated_after_optimized_arms': True},
    'single_request_long_prompt': {'eager_medians_tps': base_tps, 'graphs_only_median_tps': graph_tps,
                                 'observed_speedup_vs_eager_runs': [graph_tps / t for t in base_tps]},
    'recommended_engine_settings': {'enforce_eager': False,
                                    'compilation_config': {'mode': 0, 'cudagraph_mode': 'FULL_DECODE_ONLY'},
                                    'kernel_config': {'linear_backend': 'auto'},
                                    'selected_matrix_kernel': 'CutlassFP8ScaledMMLinearKernel'},
    'matrix_comparison_4096_prefill_budget': {'cutlass_decode_tps': case('graphs_only_cutlass_4k')['decode_tps_median'],
                                            'torch_decode_tps': case('graphs_only_torch_4k')['decode_tps_median'],
                                            'finding': 'Ranges overlap; no reliable alternate-kernel win established. Torch failed startup at the original 8192-token prefill budget, with no available KV cache memory.'},
    'numerical_check': 'Graph-only matched all six original single-request measured output sequences. Batched graph replay, compilation/fusion, and the Torch alternate produced different sequences. Token agreement is not compiler-verified matching quality.',
    'limitations': ['Two assembly-excerpt prompts from one development target; fixed 512-token microbenchmark, including tokens beyond EOS.',
                    'The machine was not isolated; eager baseline and some other arms varied materially. Speedup ratios are observations, not guaranteed production gains.',
                    'No compiler match-rate evaluation, model training, KB changes, or campaign integration.',
                    'b12x absent and its installed FP8 integration requires static per-tensor activation scaling; current online FP8 uses dynamic per-token scaling. It was not installed or benchmarked.'],
    'training_eligible': False, 'quality_evaluation': False,
    'verification': json.loads((root / 'verification.json').read_text()),
    'variants': summary,
}
(root / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: result[k] for k in ['conclusion', 'single_request_long_prompt', 'matrix_comparison_4096_prefill_budget', 'verification']}, indent=2))
