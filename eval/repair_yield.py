"""Observed repair yield by target size and profile, separate from ranking.

Counts accepted object-exact transitions, never similarity as recovered bytes.
Inference time is Ollama's recorded prompt+generation time, not GPU utilization.
"""
import math

EXACT = {'object_exact', 'integrated'}


def size_band(size):
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return 'unknown'
    return 'under_256B' if size < 256 else '256B_to_1KiB' if size < 1024 else 'at_least_1KiB'


def seconds(value):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else 0.


def record(metrics, before, after, profile, result):
    """Called once per accepted campaign work item, before its checkpoint save."""
    size = after.get('size')
    known = size_band(size) != 'unknown'
    gain = int(before.get('status') not in EXACT and after.get('status') in EXACT)
    loss = int(before.get('status') in EXACT and after.get('status') not in EXACT)
    perf = result.get('performance') or {}
    delta = dict(work_items=1, exact_functions_gained=gain, exact_functions_lost=loss,
                 exact_bytes_gained=size*gain if known else 0,
                 exact_bytes_lost=size*loss if known else 0,
                 unknown_size_items=int(not known),
                 score_improvements=int(bool(result.get('best_score_improved'))),
                 inference_seconds=seconds(perf.get('model_prompt_seconds'))+seconds(perf.get('model_generation_seconds')),
                 model_request_seconds=seconds(perf.get('model_seconds')),
                 model_queue_seconds=seconds(perf.get('model_queue_seconds')),
                 worker_seconds=seconds(result.get('wall_seconds')))
    stats = metrics.setdefault('repair_yield', {'version':1,'totals':{},'by_size':{},'by_profile':{}})
    buckets = [stats['totals'], stats['by_size'].setdefault(size_band(size), {}),
               stats['by_profile'].setdefault(profile.get('name', 'unknown'), {})]
    for bucket in buckets:
        for key, value in delta.items():
            bucket[key] = bucket.get(key, 0) + value
    return delta
