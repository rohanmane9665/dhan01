import logging
import traceback
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all exception handler for any unhandled errors in API routes.
    Logs the error with traceback and returns a clean 500 JSON response.
    """
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {str(exc)}\n"
        f"{traceback.format_exc()}"
    )
    
    # We could inspect `exc` here for specific database or Redis errors, 
    # but a generic 500 covers all unexpected crashes securely without leaking internals.
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "An unexpected internal server error occurred.",
            "detail": str(exc) if hasattr(exc, "detail") else "See server logs for details."
        },
    )

def setup_exception_handlers(app):
    """Register all custom exception handlers to the FastAPI app."""
    app.add_exception_handler(Exception, global_exception_handler)
