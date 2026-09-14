import os
import json
from fastapi import APIRouter

router = APIRouter()

LOG_FILE = "logs/trading.log"


@router.get("/")
async def get_logs(limit: int = 100):
    """Returns the last N lines from the trading log file."""
    lines = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            lines = all_lines[-limit:]
        except Exception as e:
            return {"error": str(e), "entries": []}

    entries = []
    for i, line in enumerate(reversed(lines)):
        line = line.strip()
        if not line:
            continue
        # Parse standard Python log format: YYYY-MM-DD HH:MM:SS,ms - LEVEL - message
        parts = line.split(" - ", 2)
        if len(parts) == 3:
            entries.append({
                "id": i,
                "timestamp": parts[0].strip(),
                "severity": parts[1].strip(),
                "message": parts[2].strip(),
            })
        else:
            entries.append({"id": i, "timestamp": "", "severity": "INFO", "message": line})

    return {"count": len(entries), "entries": entries}
