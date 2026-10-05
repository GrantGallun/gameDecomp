#!/bin/bash
set -u
nproc
ps -eo pid,ppid,etime,args | grep -E 'run_context|run_export|context_tasks|logic_tasks|logic_grade|logic_pilot|lora_serve.server|train_source_repair' | grep -v grep
ls -lt /home/grant/decomp/experiments/edit-capability-20261002/public | head -22
tail -12 /home/grant/decomp/experiments/edit-capability-20261002/public/selfcheck.log
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
curl -s --max-time 4 http://127.0.0.1:8101/v1/models
tail -4 /home/grant/decomp/experiments/edit-capability-20261002/public/context.log
