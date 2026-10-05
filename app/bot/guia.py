"""Guide bot for Yoly - menu generation and command parsing."""
import sys
sys.path.insert(0, '/home/claude/yoly')

from agents.guide import Guía as GuiaAgent


# Crear instancia global del agente
_guia_agent = GuiaAgent()


def generar_menu_reporte(wa_id, cuenta, saldo_actual=0):
    """
    Genera un menú interactivo para descargar reportes.

    SOLO devuelve string con menú, NO accede a BD.

    Args:
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta
        saldo_actual: Saldo disponible

    Returns:
        String con el menú formateado
    """
    return _guia_agent.generar_menu_reporte(wa_id, cuenta, saldo_actual)


def parse_respuesta_menu(wa_id, texto, cuenta=None):
    """
    Interpreta la respuesta del usuario al menú.

    SOLO parsea respuesta, NO accede a BD.

    Args:
        wa_id: Teléfono del usuario
        texto: Respuesta ("1", "2", "3", "4", "pdf", "excel")
        cuenta: Nombre de la cuenta (opcional)

    Returns:
        Dict con {"tipo": "resumen"|"pdf"|"excel"|"ultimos"|None, "accion": "..."}
    """
    return _guia_agent.parse_respuesta_menu(wa_id, texto, cuenta)


def parse_comando_borrar(wa_id, texto, gastos_actuales=None):
    """
    Interpreta un comando para borrar gastos.

    SOLO detecta y parsea comando, NO accede a BD (borrar se hace en factura_service).

    Formatos soportados:
    - "borrar ultima"
    - "borrar Cristina"
    - "borrar $3000"
    - "borrar 20261005"

    Args:
        wa_id: Teléfono del usuario
        texto: Comando de borrado
        gastos_actuales: Lista de gastos disponibles (opcional, para búsqueda)

    Returns:
        Dict con {"detectado": T/F, "criterio": "...", "valor": "...", "gasto_encontrado": {...}}
    """
    return _guia_agent.parse_comando_borrar(wa_id, texto, gastos_actuales)


def es_comando_borrar(texto):
    """Detecta si el texto es un comando para borrar."""
    return _guia_agent.es_comando_borrar(texto)
