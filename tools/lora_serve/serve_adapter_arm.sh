#!/usr/bin/env bash
# ADAPTER arm (M1): identical transport to serve_vllm.sh, except the named LoRA
# adapter is the default for requests that do not name a model.
#
#   ADAPTER_PATH=/path/to/adapter PORT=8102 bash tools/lora_serve/serve_adapter_arm.sh
#
# Optional: NAME=m1 (adapter name, default m1), plus any extra server flags.
#
# Only ONE GPU-resident server fits on this 16 GB card (the Windows-side ollama
# holds 2-4 GB and the fp8 weights are 7.5 GB). For a controlled A/B that is
# strictly identical in transport, prefer ONE process serving both arms:
#
#   ADAPTER_PATH=/path/to/adapter bash tools/lora_serve/serve_vllm.sh \
#       --adapter m1="$ADAPTER_PATH" --default-adapter none
#
#   model "qwen2.5-coder-7b"      -> base   (M0)
#   model "qwen2.5-coder-7b+m1"   -> +LoRA  (M1)
set -euo pipefail
cd /mnt/c/Code/gameDecomp
NAME=${NAME:-m1}
: "${ADAPTER_PATH:?set ADAPTER_PATH to the LoRA adapter directory}"
export HF_HOME=${HF_HOME:-/home/grant/decomp/hf-home}
export VLLM_USE_V2_MODEL_RUNNER=0
export VLLM_USE_FLASHINFER_SAMPLER=0
CUDA_HOME=${CUDA_HOME:-/home/grant/decomp/serve-venv/lib/python3.12/site-packages/nvidia/cu13}
export CUDA_HOME
export PATH="$CUDA_HOME/bin:$PATH"
exec /home/grant/decomp/serve-venv/bin/python -m tools.lora_serve.server \
  --model /home/grant/decomp/models/qwen2.5-coder-7b \
  --backend vllm --quantization fp8 \
  --adapter "$NAME=$ADAPTER_PATH" --default-adapter "$NAME" \
  --port "${PORT:-8102}" \
  --max-model-len 12288 \
  --max-num-seqs 6 \
  --gpu-memory-utilization 0.72 \
  --max-batch-size 6 \
  --max-batch-tokens 60000 \
  --max-batch-prefill-tokens 48000 \
  --receipt-log "$HOME/lora_serve_receipts.jsonl" \
  "$@"
