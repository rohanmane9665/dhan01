#!/bin/bash
echo "Starting Uvicorn web server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
