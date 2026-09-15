from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import os
from app.core.config import settings

router = APIRouter()

class LoginRequest(BaseModel):
    password: str

class LoginResponse(BaseModel):
    token: str
    message: str

@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    expected_token = getattr(settings, "API_SECRET", os.getenv("API_SECRET", "supersecret-admin-token"))
    
    if request.password != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid master password"
        )
        
    return LoginResponse(
        token=expected_token,  # Using the master secret directly as the bearer token
        message="Login successful"
    )
