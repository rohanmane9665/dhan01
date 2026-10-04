import os
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from dotenv import set_key, find_dotenv

from app.api.auth import verify_admin_token
from app.core.config import settings
from app.worker.trading_worker import get_worker

router = APIRouter()

class UpdateTokenRequest(BaseModel):
    dhan_access_token: str

@router.put("/dhan-token", status_code=status.HTTP_200_OK)
async def update_dhan_token(
    request: UpdateTokenRequest, 
    _=Depends(verify_admin_token)
):
    """
    Updates the Dhan Access Token in both the active memory and the .env file.
    """
    new_token = request.dhan_access_token.strip()
    if not new_token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")
        
    # Update in memory
    settings.DHAN_ACCESS_TOKEN = new_token
    
    # Try to find the .env file
    env_file = find_dotenv()
    if not env_file:
        # Fallback to absolute path relative to current dir, looking upwards
        current_dir = os.getcwd()
        possible_env = os.path.join(current_dir, ".env")
        if not os.path.exists(possible_env):
            possible_env = os.path.join(os.path.dirname(current_dir), ".env")
        env_file = possible_env

    # Update in .env file for persistence
    try:
        if os.path.exists(env_file):
            set_key(env_file, "DHAN_ACCESS_TOKEN", new_token)
        else:
            # If the file doesn't exist, create it and set
            with open(env_file, "w") as f:
                f.write(f"DHAN_ACCESS_TOKEN={new_token}\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write to .env file: {str(e)}")

    # Also update the Dhan client in the running worker if it exists
    worker = get_worker()
    if worker and worker.dhan_client:
        # Since Dhan client initializes with the token, we need to update it
        # The dhanhq.dhanhq class usually takes access_token in its constructor
        # We might need to re-initialize it or set the token directly
        worker.dhan_client.access_token = new_token
        # dhanhq library uses headers property to pass auth
        if hasattr(worker.dhan_client, "headers"):
            worker.dhan_client.headers["access-token"] = new_token

    return {"status": "success", "message": "Dhan Access Token updated successfully"}
