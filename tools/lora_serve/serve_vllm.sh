#!/usr/bin/env bash
# Serve with the vLLM backend (base-only by default), on 8101.
#
# The two env vars are REQUIRED on WSL2 and are also set by the backend itself:
#   VLLM_USE_V2_MODEL_RUNNER=0     -- V2 runner needs UVA, which WSL2 lacks
#   VLLM_USE_FLASHINFER_SAMPLER=0  -- avoids a FlashInfer JIT needing nvcc
set -euo pipefail
cd /mnt/c/Code/gameDecomp
export HF_HOME=/home/grant/decomp/hf-home
export VLLM_USE_V2_MODEL_RUNNER=0
export VLLM_USE_FLASHINFER_SAMPLER=0
CUDA_HOME=${CUDA_HOME:-/home/grant/decomp/serve-venv/lib/python3.12/site-packages/nvidia/cu13}
export CUDA_HOME
export PATH="$CUDA_HOME/bin:$PATH"
exec /home/grant/decomp/serve-venv/bin/python -m tools.lora_serve.server \
  --model /home/grant/decomp/models/qwen2.5-coder-7b \
  --backend vllm --quantization fp8 \
  --port "${PORT:-8101}" \
  --default-adapter none \
  --max-model-len 12288 \
  --max-num-seqs 6 \
  --gpu-memory-utilization 0.72 \
  --max-batch-size 6 \
  --max-batch-tokens 60000 \
  --max-batch-prefill-tokens 48000 \
  --receipt-log "$HOME/lora_serve_receipts.jsonl" \
  "$@"
