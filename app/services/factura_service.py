"""Service for managing invoices and expenses (facturas)."""
import logging
from datetime import datetime
import uuid
from app.core import state

logger = logging.getLogger(__name__)


def guardar_factura(wa_id, cuenta, monto, proveedor, fecha, metodo="transferencia", descripcion=""):
    """
    Guarda una factura/gasto para el usuario.

    Args:
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta
        monto: Monto de la factura
        proveedor: Nombre del proveedor/cliente
        fecha: Fecha de la factura (YYYY-MM-DD)
        metodo: Método de pago (transferencia, efectivo, etc)
        descripcion: Descripción adicional

    Returns:
        Dict con datos del gasto guardado
    """
    try:
        gastos = state.cargar_gastos(wa_id, cuenta)

        # Crear nuevo gasto
        gasto_nuevo = {
            "id": f"{datetime.now().isoformat()}_{str(uuid.uuid4())[:8]}",
            "fecha": fecha,
            "timestamp": datetime.now().isoformat(),
            "cliente": proveedor,
            "monto": float(monto),
            "metodo": metodo,
            "descripcion": descripcion,
            "categoria": "gasto",
        }

        gastos.append(gasto_nuevo)
        state.guardar_gastos(wa_id, gastos, cuenta)

        logger.info(f"[{wa_id}][{cuenta}] Factura guardada: {proveedor} ${monto}")
        return gasto_nuevo

    except Exception as e:
        logger.error(f"[{wa_id}][{cuenta}] Error guardando factura: {e}", exc_info=True)
        raise


def borrar_factura(wa_id, cuenta, criterio):
    """
    Borra un gasto según criterio (ultima, cliente, monto).

    Args:
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta
        criterio: Dict con {"tipo": "ultima" | "cliente" | "monto", "valor": ...}

    Returns:
        Dict con {"borrado": True/False, "mensaje": "...", "gasto": {...}}
    """
    try:
        gastos = state.cargar_gastos(wa_id, cuenta)
        if not gastos:
            return {"borrado": False, "mensaje": "No hay gastos para borrar", "gasto": None}

        criterio_tipo = criterio.get("tipo")
        gasto_encontrado = None

        if criterio_tipo == "ultima":
            gasto_encontrado = gastos[-1]
            gastos.pop()

        elif criterio_tipo == "cliente":
            valor = criterio.get("valor", "").lower()
            for i, g in enumerate(gastos):
                if valor in g.get("cliente", "").lower():
                    gasto_encontrado = g
                    gastos.pop(i)
                    break

        elif criterio_tipo == "monto":
            valor = float(criterio.get("valor", 0))
            for i, g in enumerate(gastos):
                if g.get("monto") == valor:
                    gasto_encontrado = g
                    gastos.pop(i)
                    break

        if gasto_encontrado:
            state.guardar_gastos(wa_id, gastos, cuenta)
            logger.info(f"[{wa_id}][{cuenta}] Gasto borrado: {gasto_encontrado['cliente']} ${gasto_encontrado['monto']}")
            return {
                "borrado": True,
                "mensaje": f"Borrado: {gasto_encontrado['cliente']} ${gasto_encontrado['monto']:,.0f}",
                "gasto": gasto_encontrado
            }
        else:
            return {"borrado": False, "mensaje": f"No encontré gasto con ese criterio", "gasto": None}

    except Exception as e:
        logger.error(f"[{wa_id}][{cuenta}] Error borrando factura: {e}", exc_info=True)
        return {"borrado": False, "mensaje": f"Error: {str(e)}", "gasto": None}


def get_saldo(wa_id, cuenta):
    """
    Obtiene el saldo total (suma de gastos).

    Args:
        wa_id: Teléfono del usuario
        cuenta: Nombre de la cuenta

    Returns:
        Float con el total de gastos
    """
    try:
        gastos = state.cargar_gastos(wa_id, cuenta)
        return sum(g.get("monto", 0) for g in gastos)
    except Exception as e:
        logger.error(f"[{wa_id}][{cuenta}] Error calculando saldo: {e}")
        return 0
