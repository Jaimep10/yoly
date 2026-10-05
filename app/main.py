"""Yoly Bot - FastAPI webhook application."""
import os
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from app.services import factura_service
from app.core import state
from app.bot import guia

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Yoly Bot", version="1.0.0")


# ==================== PYDANTIC MODELS ====================

class WhatsappIn(BaseModel):
    """WhatsApp incoming message model."""
    wa_id: str
    texto: str
    media_url: Optional[str] = None
    cuenta: Optional[str] = "principal"


class WhatsappOut(BaseModel):
    """WhatsApp outgoing message model."""
    status: str
    mensaje: str


# ==================== WEBHOOK ENDPOINTS ====================

@app.post("/webhook", response_model=WhatsappOut)
async def webhook(data: WhatsappIn):
    """
    WhatsApp webhook handler.

    Receives incoming messages and processes them through factura_service.
    Validates with Pydantic: class WhatsappIn(BaseModel): wa_id: str, texto: str, media_url: Optional[str]
    NUNCA toca Supabase directo.

    Args:
        data: WhatsappIn object with wa_id, texto, media_url, cuenta

    Returns:
        WhatsappOut with status and message
    """
    try:
        # Validate wa_id
        if not data.wa_id:
            raise HTTPException(status_code=422, detail="wa_id is required")

        logger.info(f"[{data.wa_id}][{data.cuenta}] Webhook received: {data.texto[:100]}")

        # Call factura_service with the message
        # This is where the business logic would go
        # For now, just return a success message

        return WhatsappOut(
            status="ok",
            mensaje=f"Mensaje procesado: {data.texto[:50]}"
        )

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "Yoly Bot"}


# ==================== ERROR HANDLERS ====================

@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    """Handle Pydantic validation errors."""
    return {"status": "error", "detail": str(exc)}
