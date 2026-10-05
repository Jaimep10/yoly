# Agente 5 - "El Guía": responde preguntas de usuarios sobre las características de Yoly y soporte.
# Detecta si el mensaje es una pregunta (vs una transacción con fotos)
# y genera respuestas paso a paso con emojis.

from anthropic import Anthropic


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

    def responder(self, texto, phone, memoria, modelo="claude-3-5-sonnet-20241022"):
        """Generate guide response for user question."""
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
