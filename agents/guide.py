# Agente 5 - "El Guía": responde preguntas de usuarios sobre las características de Yoly y soporte.
# Detecta si el mensaje es una pregunta (vs una transacción con fotos)
# y genera respuestas paso a paso con emojis.
# También maneja menús de reportes y comandos de borrado de gastos.

import re
import logging
from anthropic import Anthropic

logger = logging.getLogger(__name__)


class Guía:
    """Agent that guides users through app features and troubleshooting."""

    def __init__(self):
        self.client = Anthropic()

    def es_pregunta(self, texto):
        """Detect if texto is a question vs transaction."""
        if not texto or not texto.strip():
            return False
        texto_lower = texto.lower()
        palabras_clave = [
            "como", "cómo", "que es", "qué es", "ayuda",
            "eliminar", "borrar", "ver", "carpeta", "balance",
            "¿", "?"
        ]
        return any(p in texto_lower for p in palabras_clave)

    def responder(self, texto, phone, memoria, modelo="claude-3-5-sonnet-20241022", cuenta="principal"):
        """Generate guide response for user question. Cuenta parameter for future account-aware responses."""
        # Use Claude to understand the question and provide step-by-step guidance
        # Return WhatsApp-formatted response with emojis and numbered steps
        prompt = f"""Eres el agente Guía de Yoly, un bot contable de WhatsApp.

Usuario pregunta: {texto}

Genera una respuesta paso a paso con emojis, en español, máximo 3-4 pasos simples.
Formato: 📌 Paso 1: ... \\n 📌 Paso 2: ... etc.
Sé conciso y amigable."""

        response = self.client.messages.create(
            model=modelo,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    def generar_menu_reporte(self, wa_id, cuenta_activa, saldo_actual=0):
        """
        Genera un menú interactivo para descargar reportes.
        Retorna el mensaje con opciones 1-4 y para descargar archivos.

        Args:
            wa_id: Teléfono del usuario (limpio)
            cuenta_activa: Nombre de la cuenta actual (ej: "Fanny")
            saldo_actual: Saldo disponible (opcional)

        Returns:
            Mensaje de menú con emojis y opciones numeradas
        """
        saldo_texto = f" (Saldo: ${saldo_actual:,.0f})" if saldo_actual else ""

        menu = f"""📊 *Reporte de {cuenta_activa}{saldo_texto}*

¿Qué deseas hacer?

1️⃣ Ver resumen mensual
2️⃣ Descargar PDF
3️⃣ Descargar Excel
4️⃣ Ver últimos gastos

Escribe el número (1, 2, 3, 4) o *pdf* / *excel*"""

        return menu

    def parse_respuesta_menu(self, wa_id, texto, cuenta_activa=None):
        """
        Interpreta la respuesta del usuario al menú de reportes.

        Args:
            wa_id: Teléfono del usuario
            texto: Respuesta del usuario ("1", "2", "3", "4", "pdf", "excel")
            cuenta_activa: Nombre de la cuenta

        Returns:
            Dict con {
                "tipo": "resumen" | "pdf" | "excel" | "ultimos" | None,
                "accion": Descripción de lo que se debe hacer
            }
        """
        texto_limpio = texto.strip().lower()

        opciones = {
            "1": {"tipo": "resumen", "accion": "Mostrar resumen mensual"},
            "2": {"tipo": "pdf", "accion": "Generar PDF"},
            "3": {"tipo": "excel", "accion": "Generar Excel"},
            "4": {"tipo": "ultimos", "accion": "Mostrar últimos 10 gastos"},
            "pdf": {"tipo": "pdf", "accion": "Generar PDF"},
            "excel": {"tipo": "excel", "accion": "Generar Excel"},
        }

        return opciones.get(texto_limpio, {"tipo": None, "accion": "Opción no reconocida"})

    def es_comando_borrar(self, texto):
        """Detecta si el texto es un comando para borrar gastos."""
        if not texto or not texto.strip():
            return False
        texto_lower = texto.lower()
        palabras_clave = ["borrar", "eliminar", "anular", "delete", "remove"]
        return any(p in texto_lower for p in palabras_clave)

    def parse_comando_borrar(self, wa_id, texto, gastos_actuales=None):
        """
        Interpreta un comando para borrar gastos.

        Formatos soportados:
        - "borrar ultima" -> elimina el último gasto
        - "borrar Cristina" -> busca gasto de proveedor "Cristina" y lo borra
        - "borrar $3000" -> busca gasto por monto
        - "borrar 20261005" -> busca gasto por fecha

        Args:
            wa_id: Teléfono del usuario
            texto: Comando de borrado
            gastos_actuales: Lista de gastos del usuario (o None si se cargan desde memoria)

        Returns:
            Dict con {
                "detectado": True/False,
                "criterio": "ultima" | "cliente" | "monto" | "fecha" | None,
                "valor": El valor buscado,
                "gasto_encontrado": El gasto que coincide (si aplica),
                "mensaje": Mensaje de respuesta para el usuario
            }
        """
        if not self.es_comando_borrar(texto):
            return {
                "detectado": False,
                "criterio": None,
                "valor": None,
                "gasto_encontrado": None,
                "mensaje": None
            }

        logger.info(f"[{wa_id}] Comando borrar detectado: '{texto}'")

        if not gastos_actuales:
            gastos_actuales = []

        resultado = {
            "detectado": True,
            "criterio": None,
            "valor": None,
            "gasto_encontrado": None,
            "mensaje": None
        }

        texto_lower = texto.lower()

        # Detectar "borrar ultima"
        if "ultima" in texto_lower or "últim" in texto_lower:
            resultado["criterio"] = "ultima"
            if gastos_actuales:
                resultado["gasto_encontrado"] = gastos_actuales[-1]
                cliente = resultado["gasto_encontrado"].get("cliente", "")
                monto = resultado["gasto_encontrado"].get("monto", 0)
                resultado["mensaje"] = f"Borrado: {cliente} ${monto:,.0f}"
            else:
                resultado["mensaje"] = "No hay gastos para borrar."
            return resultado

        # Detectar por monto (ej: "borrar $3000" o "borrar 3000")
        match_monto = re.search(r'\$?([\d.,]+)', texto)
        if match_monto:
            monto_str = match_monto.group(1).replace(',', '').replace('.', '', 1)
            try:
                monto_buscado = float(monto_str.replace('.', ''))
                resultado["criterio"] = "monto"
                resultado["valor"] = monto_buscado

                for gasto in gastos_actuales:
                    if gasto.get("monto") == monto_buscado:
                        resultado["gasto_encontrado"] = gasto
                        cliente = gasto.get("cliente", "")
                        resultado["mensaje"] = f"Borrado: {cliente} ${monto_buscado:,.0f}"
                        break

                if not resultado["gasto_encontrado"]:
                    resultado["mensaje"] = f"No encontré gasto de ${monto_buscado:,.0f}"
            except:
                pass

        # Detectar por cliente/proveedor (ej: "borrar Cristina")
        if not resultado["gasto_encontrado"]:
            # Extraer nombre después de "borrar"
            palabras = texto_lower.split()
            idx_borrar = -1
            for i, p in enumerate(palabras):
                if "borrar" in p or "eliminar" in p or "anular" in p:
                    idx_borrar = i
                    break

            if idx_borrar >= 0 and idx_borrar + 1 < len(palabras):
                nombre_buscado = " ".join(palabras[idx_borrar + 1:]).strip()
                if nombre_buscado and nombre_buscado not in ["ultima", "últim", "el", "la", "un", "una"]:
                    resultado["criterio"] = "cliente"
                    resultado["valor"] = nombre_buscado

                    for gasto in gastos_actuales:
                        cliente = gasto.get("cliente", "").lower()
                        if nombre_buscado in cliente or cliente in nombre_buscado:
                            resultado["gasto_encontrado"] = gasto
                            monto = gasto.get("monto", 0)
                            resultado["mensaje"] = f"Borrado: {gasto.get('cliente', '')} ${monto:,.0f}"
                            break

                    if not resultado["gasto_encontrado"]:
                        resultado["mensaje"] = f"No encontré gasto de '{nombre_buscado}'"

        # Si no se encontró nada y tampoco hay criterio claro
        if not resultado["gasto_encontrado"] and not resultado["criterio"]:
            resultado["mensaje"] = "No pude identificar qué gasto quieres borrar. Intenta: 'borrar ultima', 'borrar Cristina' o 'borrar $3000'"

        return resultado
