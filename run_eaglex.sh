#!/bin/bash
cd /workspace/project/EAGLE-X-/backend
export FRONTEND_DIR=/workspace/project/EAGLE-X-/frontend/out
mkdir -p /workspace/project/EAGLE-X-/.run
LOG=/workspace/project/EAGLE-X-/.run/eaglex.log
while true; do
  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 12000 >> "$LOG" 2>&1
  echo "$(date -Iseconds) uvicorn exited; restarting in 2s" >> "$LOG"
  sleep 2
done
