import os
from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

security = HTTPBearer(auto_error=False)

def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    token: str = Query(None)
):
    """Simple token verification for sensitive endpoints (Kill Switch, Manual Exits, SSE)."""
    expected_token = getattr(settings, "API_SECRET", os.getenv("API_SECRET", "supersecret-admin-token"))
    
    provided_token = None
    if credentials:
        provided_token = credentials.credentials
    elif token:
        provided_token = token
        
    if provided_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return provided_token
