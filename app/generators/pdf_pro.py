"""Professional PDF generator for Yoly reports."""
import logging

logger = logging.getLogger(__name__)


def create_pdf_profesional(wa_id, cuenta, gastos=None):
    """
    Crea un PDF profesional con logo y diseño Yoly.

    Args:
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta
        gastos: Lista de gastos

    Returns:
        bytes (PDF content) or None
    """
    # Placeholder - PDF generation not yet implemented
    logger.warning(f"[{wa_id}][{cuenta}] PDF generation not yet implemented")
    return None
