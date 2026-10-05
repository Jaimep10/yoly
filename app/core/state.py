"""State management for Yoly Bot - user context and account data."""
import os
import json
import logging

logger = logging.getLogger(__name__)

DATA_DIR = os.getenv('DATA_DIR', '/home/claude/yoly/data')


def obtener_ruta_datos(telefono, cuenta="principal"):
    """Obtiene la ruta de la carpeta de datos del usuario."""
    ruta = f'{DATA_DIR}/{telefono}/{cuenta}'
    os.makedirs(ruta, exist_ok=True)
    return ruta


def get_estado(wa_id, cuenta="principal"):
    """
    Obtiene el estado actual del usuario.

    Args:
        wa_id: Teléfono del usuario (limpio)
        cuenta: Nombre de la cuenta

    Returns:
        Dict con datos del usuario (gastos, memoria, estado)
    """
    ruta = obtener_ruta_datos(wa_id, cuenta)

    # Cargar datos del usuario
    gastos = cargar_gastos(wa_id, cuenta)
    memoria = cargar_memoria(wa_id, cuenta)

    return {
        "wa_id": wa_id,
        "cuenta": cuenta,
        "gastos": gastos,
        "memoria": memoria,
        "saldo": sum(g.get("monto", 0) for g in gastos)
    }


def set_estado(wa_id, cuenta, estado, esperando_menu=False):
    """
    Guarda el estado del usuario.

    Args:
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta
        estado: Nuevo estado del usuario
        esperando_menu: Si está esperando respuesta del menú
    """
    ruta = obtener_ruta_datos(wa_id, cuenta)

    # Guardar memoria con el nuevo estado
    memoria = cargar_memoria(wa_id, cuenta)
    memoria[wa_id] = {
        **memoria.get(wa_id, {}),
        "estado": estado,
        "esperando_menu": esperando_menu,
        "updated_at": __import__('datetime').datetime.now().isoformat()
    }
    guardar_memoria(memoria, wa_id, cuenta)


def cargar_gastos(telefono, cuenta="principal"):
    """Carga los gastos guardados del usuario."""
    ruta = obtener_ruta_datos(telefono, cuenta)
    archivo = f'{ruta}/gastos.json'
    if os.path.exists(archivo):
        try:
            with open(archivo, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error cargando gastos: {e}")
            return []
    return []


def guardar_gastos(telefono, gastos, cuenta="principal"):
    """Guarda los gastos del usuario."""
    ruta = obtener_ruta_datos(telefono, cuenta)
    archivo = f'{ruta}/gastos.json'
    try:
        os.makedirs(os.path.dirname(archivo), exist_ok=True)
        with open(archivo, 'w', encoding='utf-8') as f:
            json.dump(gastos, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error guardando gastos: {e}")
        return False


def cargar_memoria(phone_clean="", cuenta="principal"):
    """Carga memoria del usuario."""
    if phone_clean:
        ruta = obtener_ruta_datos(phone_clean, cuenta)
        memoria_archivo = f'{ruta}/memoria_{phone_clean}_{cuenta}.json'
    else:
        memoria_archivo = f'{DATA_DIR}/memoria_global.json'

    if os.path.exists(memoria_archivo):
        try:
            with open(memoria_archivo, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error cargando memoria: {e}")
            return {}
    return {}


def guardar_memoria(memoria, phone_clean="", cuenta="principal"):
    """Guarda memoria del usuario."""
    if phone_clean:
        ruta = obtener_ruta_datos(phone_clean, cuenta)
        memoria_archivo = f'{ruta}/memoria_{phone_clean}_{cuenta}.json'
    else:
        memoria_archivo = f'{DATA_DIR}/memoria_global.json'

    try:
        os.makedirs(os.path.dirname(memoria_archivo), exist_ok=True)
        with open(memoria_archivo, 'w', encoding='utf-8') as f:
            json.dump(memoria, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Error guardando memoria: {e}")
