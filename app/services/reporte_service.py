"""Service for generating reports (PDF, Excel)."""
import logging
from app.core import state
from app.generators import excel_pro

logger = logging.getLogger(__name__)


def generar(tipo, wa_id, cuenta):
    """
    Genera un reporte del tipo solicitado.

    Args:
        tipo: "link" | "pdf" | "excel_pro"
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta

    Returns:
        Resultado según el tipo (workbook para excel, bytes para pdf, etc)
    """
    try:
        if tipo == "excel_pro":
            gastos = state.cargar_gastos(wa_id, cuenta)
            wb = excel_pro.create_excel_profesional(wa_id, cuenta, gastos)
            logger.info(f"[{wa_id}][{cuenta}] Excel generado: {len(gastos)} gastos")
            return wb

        elif tipo == "pdf":
            # Placeholder para PDF profesional
            logger.warning(f"[{wa_id}][{cuenta}] PDF generation not yet implemented")
            return None

        elif tipo == "link":
            # Placeholder para generar un link de descarga
            logger.warning(f"[{wa_id}][{cuenta}] Link generation not yet implemented")
            return None

        else:
            logger.error(f"[{wa_id}][{cuenta}] Tipo de reporte desconocido: {tipo}")
            return None

    except Exception as e:
        logger.error(f"[{wa_id}][{cuenta}] Error generando reporte {tipo}: {e}", exc_info=True)
        return None
