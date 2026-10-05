"""Professional Excel generator for Yoly reports."""
import sys
import os

# Add parent directory to path to import generador_excel
sys.path.insert(0, '/home/claude/yoly')

from generador_excel import crear_excel_profesional


def create_excel_profesional(wa_id, cuenta, gastos=None, cobro=None):
    """
    Crea un Excel profesional con 5 pestañas.

    Wrapper around generador_excel.crear_excel_profesional para mantener compatibilidad.

    Args:
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta
        gastos: Lista de gastos
        cobro: Datos de cobro/deuda (opcional)

    Returns:
        Workbook object (openpyxl)
    """
    if gastos is None:
        gastos = []
    if cobro is None:
        cobro = {}

    return crear_excel_profesional(wa_id, cuenta, gastos, cobro)
