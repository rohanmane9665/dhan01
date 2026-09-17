#!/bin/bash
echo "Starting Trading Worker in background..."
python -m app.worker_main &

echo "Starting Uvicorn web server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
