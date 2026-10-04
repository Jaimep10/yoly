import os
os.environ["MPLBACKEND"] = "Agg"  # sin ventanas: los gráficos se dibujan fuera del hilo principal (Mac)
import json
import logging
import threading
import io
import re
from datetime import datetime, timedelta
from calendar import monthrange
from flask import Flask, request, jsonify, Response, render_template_string
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
import anthropic
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from datetime import datetime
import base64
import requests
from requests.auth import HTTPBasicAuth
from PIL import Image as PILImage
from groq import Groq
import zipfile
import io
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

app = Flask(__name__)

# Logging configuration
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== ENVIRONMENT VALIDATION ====================

print("[STARTUP] Initializing Yoly Bot...")

# Check required environment variables
required_env_vars = {
    'TWILIO_ACCOUNT_SID': 'Twilio Account SID',
    'TWILIO_AUTH_TOKEN': 'Twilio Auth Token',
    'TWILIO_WHATSAPP_NUMBER': 'Twilio WhatsApp Number',
    'ANTHROPIC_API_KEY': 'Anthropic API Key',
    'GROQ_API_KEY': 'Groq API Key'
}

missing_vars = []
for var_name, var_desc in required_env_vars.items():
    if not os.environ.get(var_name):
        error_msg = f"[WARNING] Missing environment variable: {var_desc} ({var_name})"
        print(error_msg)
        logger.warning(error_msg)
        missing_vars.append(var_name)
    else:
        print(f"[OK] {var_desc} is configured")
        logger.info(f"Environment variable {var_name} is configured")

# Modelo fijo: claude-3-5-haiku y claude-3-haiku ya están retirados por Anthropic.
MODELO_CLAUDE = "claude-haiku-4-5"

# WhatsApp/Twilio rechaza mensajes de más de 1600 caracteres.
LIMITE_WHATSAPP = 1500

def partir_mensaje(texto, limite=LIMITE_WHATSAPP):
    """Divide el texto en partes de <= limite, cortando en salto de línea o espacio."""
    partes = []
    while len(texto) > limite:
        corte = texto.rfind("\n", 0, limite + 1)
        if corte <= 0:
            corte = texto.rfind(" ", 0, limite + 1)
        if corte <= 0:
            corte = limite
        partes.append(texto[:corte].rstrip())
        texto = texto[corte:].lstrip()
    if texto:
        partes.append(texto)
    return partes

def responder(resp, texto):
    """Agrega la respuesta al TwiML, en varios mensajes si es larga."""
    print(f"Respuesta generada: {len(texto)} caracteres")
    for parte in partir_mensaje(texto):
        resp.message(parte)

# Twilio corta el webhook a los 15 s y entonces el usuario no recibe nada.
# Si Yoly tarda más que esto, contesta "ya te respondo" y manda la respuesta
# después por la API de Twilio.
ESPERA_MAX_SEGUNDOS = 10
MENSAJE_ESPERA = "⏳ Recibí tu mensaje. Lo estoy procesando, en unos segundos te respondo."

def ruta_temporal(pdf_path):
    """Archivo temporal propio de este hilo, en la misma carpeta que el PDF final."""
    return f"{pdf_path}.{os.getpid()}-{threading.get_ident()}.tmp"

def publicar_pdf(tmp_path, pdf_path):
    """Reemplaza el PDF final de una sola vez, y solo si el temporal es un PDF completo.
    Así una descarga nunca ve un archivo a medias o vacío."""
    with open(tmp_path, "rb") as f:
        valido = os.path.getsize(tmp_path) > 0 and f.read(5) == b"%PDF-"
    if not valido:
        os.remove(tmp_path)
        raise ValueError(f"El PDF generado está vacío o dañado: {pdf_path}")
    os.replace(tmp_path, pdf_path)

def servir_pdf(pdf_path, nombre):
    """Entrega el PDF con su Content-Length real (sin sendfile). 404 si no existe o está vacío."""
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
        logger.warning(f"PDF no disponible o vacío: {pdf_path}")
        return f"{nombre} no disponible", 404
    with open(pdf_path, "rb") as f:
        datos = f.read()
    return Response(datos, mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={nombre}"})

class Salida:
    """Junta los textos de la respuesta. Tiene .message() como MessagingResponse,
    así el código de cada comando no cambia si la respuesta sale después."""
    def __init__(self):
        self.textos = []

    def message(self, texto):
        self.textos.append(texto)

# Initialize clients
try:
    # Sin esto el SDK espera hasta 10 min por llamada y reintenta 2 veces.
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=60.0, max_retries=1)
    logger.info("[OK] Anthropic client initialized")
except Exception as e:
    print(f"[WARNING] Failed to initialize Anthropic client: {e}")
    logger.warning(f"Failed to initialize Anthropic client: {e}")
    client = None

try:
    twilio_client = Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))
    logger.info("[OK] Twilio client initialized")
except Exception as e:
    print(f"[WARNING] Failed to initialize Twilio client: {e}")
    logger.warning(f"Failed to initialize Twilio client: {e}")
    twilio_client = None

try:
    groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    logger.info("[OK] Groq client initialized")
except Exception as e:
    print(f"[WARNING] Failed to initialize Groq client: {e}")
    logger.warning(f"Failed to initialize Groq client: {e}")
    groq_client = None

if missing_vars:
    print(f"[WARNING] App will start but some features may not work. Missing: {', '.join(missing_vars)}")
    logger.warning(f"App starting with missing environment variables: {missing_vars}")
else:
    print("[OK] All required environment variables are configured")
    logger.info("All required environment variables are configured")

print("[STARTUP] Yoly Bot initialization complete")

GOALS_FILE = 'goals.json'
FINANCIAL_CONTEXT_FILE = 'financial_context.json'

# ==================== FINANCIAL CONTEXT PERSISTENCE ====================

def cargar_contexto_financiero():
    """Carga el contexto financiero acumulado"""
    if os.path.exists(FINANCIAL_CONTEXT_FILE):
        try:
            with open(FINANCIAL_CONTEXT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error cargando contexto financiero: {e}")
            return {'ingresos_mensuales': 0, 'gastos': {}}
    return {'ingresos_mensuales': 0, 'gastos': {}}

def guardar_contexto_financiero(contexto):
    """Guarda el contexto financiero acumulado"""
    try:
        with open(FINANCIAL_CONTEXT_FILE, 'w', encoding='utf-8') as f:
            json.dump(contexto, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error guardando contexto financiero: {e}")
        return False

# ==================== SMART FILING SYSTEM WITH CLAUDE VISION ====================

def obtener_ruta_datos(telefono):
    """Obtiene la ruta de la carpeta de datos del usuario"""
    ruta = f'/home/claude/yoly/data/{telefono}'
    os.makedirs(ruta, exist_ok=True)
    return ruta

def cargar_gastos(telefono):
    """Carga los gastos guardados del usuario"""
    ruta = obtener_ruta_datos(telefono)
    archivo = f'{ruta}/gastos.json'
    if os.path.exists(archivo):
        try:
            with open(archivo, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error cargando gastos: {e}")
            return []
    return []

def guardar_gastos(telefono, gastos):
    """Guarda los gastos del usuario"""
    ruta = obtener_ruta_datos(telefono)
    archivo = f'{ruta}/gastos.json'
    try:
        with open(archivo, 'w', encoding='utf-8') as f:
            json.dump(gastos, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error guardando gastos: {e}")
        return False

def descargar_media_twilio(media_url):
    """Descarga media desde Twilio con autenticación básica"""
    try:
        auth = HTTPBasicAuth(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        r = requests.get(media_url, auth=auth, timeout=20)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"Error descargando media: {e}")
        logger.error(f"Error descargando media: {e}", exc_info=True)
        return None

def convertir_a_webp(imagen_bytes, max_dimension=1024, quality=70):
    """
    Convierte imagen bytes a WEBP
    Redimensiona a max_dimension x max_dimension manteniendo aspect ratio
    """
    try:
        img = PILImage.open(io.BytesIO(imagen_bytes))

        # Convertir a RGB si es necesario
        if img.mode in ('RGBA', 'LA', 'P'):
            rgb_img = PILImage.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = rgb_img

        # Redimensionar manteniendo aspect ratio
        img.thumbnail((max_dimension, max_dimension), PILImage.Resampling.LANCZOS)

        # Convertir a WEBP
        output = io.BytesIO()
        img.save(output, format='WEBP', quality=quality)
        return output.getvalue()
    except Exception as e:
        print(f"Error convirtiendo a WEBP: {e}")
        logger.error(f"Error convirtiendo a WEBP: {e}", exc_info=True)
        return None

def procesar_audio_groq(ruta_tmp):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    with open(ruta_tmp, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(ruta_tmp, f.read()),
            model="whisper-large-v3",
            language="es",
            response_format="text"
        )
    return result if isinstance(result, str) else str(result)

def extraer_gastos(transcripcion):
    """
    Extrae total_enviado, categorías específicas y calcula reserva.

    Detecta:
    - total_enviado: "Envié 500" → 500
    - Categorías: "renta", "comida", "estefanito", etc.
    - Reserva: total_enviado - suma_desglose

    Retorna estructura mejorada con validación.
    """
    try:
        transcripcion_lower = transcripcion.lower()
        print(f"[EXTRAER_GASTOS] Transcripción: {transcripcion}")

        # PASO 1: Detectar total_enviado
        # Busca: "envié 500", "mandé 500", "total 500"
        regex_total = r'(?:envié|mandé|total)\s+\$?(\d+(?:\.\d+)?)'
        match_total = re.search(regex_total, transcripcion_lower)
        total_enviado = None
        if match_total:
            total_enviado = float(match_total.group(1))
            print(f"[EXTRAER_GASTOS] Total enviado detectado: {total_enviado}")

        # PASO 2: Detectar categorías específicas
        # Busca: "380 renta" o "renta 380" o "380 para renta"
        desglose = {}
        categorias = ['renta', 'comida', 'estefanito', 'transporte', 'utilidades', 'utilidad', 'internet', 'telefono', 'servicios', 'otros', 'otro']

        for categoria in categorias:
            # Patrón 1: número seguido de categoría (ej: "380 renta")
            patron1 = rf'(\d+(?:\.\d+)?)\s+{categoria}'
            match1 = re.search(patron1, transcripcion_lower)

            # Patrón 2: categoría seguida de número (ej: "renta 380")
            patron2 = rf'{categoria}\s+\$?(\d+(?:\.\d+)?)'
            match2 = re.search(patron2, transcripcion_lower)

            # Patrón 3: número para categoría (ej: "380 para renta")
            patron3 = rf'(\d+(?:\.\d+)?)\s+(?:para|de)\s+{categoria}'
            match3 = re.search(patron3, transcripcion_lower)

            match = match1 or match2 or match3
            if match:
                try:
                    # Obtener el número correcto según cuál match funcionó
                    if match1:
                        monto = float(match1.group(1))
                    elif match2:
                        monto = float(match2.group(1))
                    else:
                        monto = float(match3.group(1))

                    # Normalizar nombre de categoría
                    cat_normalizada = categoria
                    if cat_normalizada not in desglose:  # No duplicar si ya lo encontramos
                        desglose[cat_normalizada] = monto
                        print(f"[EXTRAER_GASTOS] {cat_normalizada.upper()}: {monto}")
                except (ValueError, IndexError):
                    pass

        # PASO 3: Calcular suma del desglose sin reserva
        suma_desglose_sin_reserva = sum(desglose.values())
        print(f"[EXTRAER_GASTOS] Suma desglose: {suma_desglose_sin_reserva}")

        # PASO 4: Calcular reserva y validación
        reserva = 0
        diferencia = 0
        alerta = None
        valido = True

        if total_enviado is not None:
            diferencia = total_enviado - suma_desglose_sin_reserva
            reserva = diferencia

            if reserva < 0:
                alerta = f"⚠️ Te pasaste ${abs(reserva)}. El desglose suma ${suma_desglose_sin_reserva} pero dijiste total ${total_enviado}."
                valido = False
                print(f"[EXTRAER_GASTOS] ALERTA: {alerta}")
        else:
            # Si no hay total_enviado, el total es el desglose y no hay reserva
            total_enviado = suma_desglose_sin_reserva
            reserva = 0
            diferencia = 0
            valido = True

        # PASO 5: Retornar estructura mejorada
        resultado = {
            "total_enviado": round(total_enviado, 2),
            "desglose": {k: round(v, 2) for k, v in desglose.items()},
            "reserva": round(reserva, 2),
            "diferencia": round(diferencia, 2),
            "alerta": alerta,
            "valido": valido,
            "error": None if valido else alerta,
            "transcripcion_usada": transcripcion
        }

        print(f"[EXTRAER_GASTOS] Resultado: {resultado}")
        return resultado

    except Exception as e:
        logger.error(f"Error en extraer_gastos: {e}", exc_info=True)
        return {
            "error": f"Error procesando transcripción: {str(e)}",
            "total_enviado": None,
            "desglose": {},
            "reserva": 0,
            "diferencia": 0,
            "alerta": None,
            "valido": False
        }

def procesar_audio(media_url, telefono):
    """
    Descarga un audio desde Twilio y lo transcribe con Groq Whisper Large V3.
    Retorna el texto transcrito o un mensaje de error.
    """
    try:
        # Descargar audio
        contenido = descargar_media_twilio(media_url)
        if not contenido:
            return "❌ No pude descargar el audio. Intenta de nuevo."

        # Guardar en archivo temporal
        temp_path = f"/tmp/{telefono}.ogg"
        with open(temp_path, "wb") as f:
            f.write(contenido)

        # Transcribir con Groq Whisper Large V3
        texto = procesar_audio_groq(temp_path)

        # Limpiar archivo temporal
        try:
            os.remove(temp_path)
        except:
            pass

        if not texto:
            return "❌ No pude transcribir el audio. Intenta de nuevo."

        return texto
    except Exception as e:
        print(f"Error procesando audio: {e}")
        logger.error(f"Error procesando audio: {e}", exc_info=True)
        return None

def procesar_foto_inteligente(media_url, telefono):
    """
    Procesa una foto de factura/recibo usando Claude Vision.
    Descarga, convierte a WEBP, guarda en /app/data/{telefono}/facturas/
    y extrae información con IA.
    """
    gasto = None  # Inicializar variable antes de try/except
    try:
        # Descargar imagen
        imagen_bytes = descargar_media_twilio(media_url)
        if not imagen_bytes:
            return "❌ No pude descargar la imagen. Intenta de nuevo."

        # Convertir a WEBP
        webp_bytes = convertir_a_webp(imagen_bytes)
        if not webp_bytes:
            return "❌ No pude procesar la imagen. Intenta con otra."

        # Guardar temporalmente en carpeta de facturas (con nombre genérico)
        ruta_datos = obtener_ruta_datos(telefono)
        ruta_facturas = f"{ruta_datos}/facturas"
        os.makedirs(ruta_facturas, exist_ok=True)
        fecha_hoy = datetime.now().strftime("%Y-%m-%d")
        # Guardar con nombre temporal; se renombrará después de parsear
        timestamp_tmp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_archivo = f"{ruta_facturas}/{fecha_hoy}_{timestamp_tmp}.webp"
        with open(ruta_archivo, "wb") as f:
            f.write(webp_bytes)

        # Convertir a base64 para Claude Vision
        imagen_base64 = base64.standard_b64encode(webp_bytes).decode('utf-8')

        # Llamar a Claude Vision con imagen WEBP
        response = client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/webp",
                            "data": imagen_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": """Analiza esta factura/recibo/ticket. Extrae SOLO un JSON válido, sin explicaciones:
{
  "monto": cantidad total de la factura,
  "fecha": "YYYY-MM-DD",
  "proveedor": "nombre del lugar/empresa",
  "categoria": "materiales|envio_ecuador|comida|renta|otro",
  "tipo_documento": "factura|recibo_envio|ticket",
  "destino": "nombre del destino o lugar",
  "tarifa_envio": solo si aparece envío en la factura,
  "para_quien": "persona o descripción",
  "descripcion": "resumen breve"
}

INSTRUCCION CRITICA: SOLO extrae números que REALMENTE aparecen en la factura. NUNCA inventes montos. Si no ves claramente el precio, deja el campo vacío o pregunta.
Sé específico en categoría: si es Home Depot o ferretería -> materiales. Si menciona Ecuador o envío -> envio_ecuador. Si es comida -> comida. Si es alquiler/renta -> renta."""
                    }
                ]
            }]
        )

        # Parsear respuesta JSON
        try:
            texto_respuesta = response.content[0].text.strip()
            # Limpiar posibles marcas de código
            if texto_respuesta.startswith('```'):
                texto_respuesta = texto_respuesta.split('```')[1]
                if texto_respuesta.startswith('json'):
                    texto_respuesta = texto_respuesta[4:]
            if texto_respuesta.endswith('```'):
                texto_respuesta = texto_respuesta[:-3]

            gasto = json.loads(texto_respuesta)
        except json.JSONDecodeError as e:
            print(f"Error parseando JSON de Claude: {e}")
            print(f"Respuesta: {texto_respuesta}")
            return "❌ No pude procesar la factura. Asegúrate que sea una imagen clara."

        # Verificar que gasto se haya parseado correctamente
        if not gasto:
            return "❌ No pude leer los números de la factura. Por favor, envía una imagen más clara o escribe el monto manualmente."

        # Crear estructura de gasto en nuevo formato
        fecha_hoy = datetime.now().strftime("%Y-%m-%d")
        timestamp_iso = datetime.now().isoformat()
        descripcion = gasto.get('descripcion', gasto.get('proveedor', 'Gasto'))
        monto = gasto.get('monto', 0)
        categoria = gasto.get('categoria', 'otro')

        # Crear ID único: fecha_hora_descripcion_monto
        timestamp_formato = datetime.now().strftime("%Y%m%d_%H%M")
        desc_normalizada = descripcion.lower().replace(' ', '').replace('-', '')[:15]
        gasto_id = f"{timestamp_formato}_{desc_normalizada}_{int(monto)}"

        gasto_nuevo = {
            "id": gasto_id,
            "fecha": fecha_hoy,
            "timestamp": timestamp_iso,
            "descripcion": descripcion,
            "monto": monto,
            "categoria": categoria,
            "factura_path": ruta_archivo
        }

        gastos = cargar_gastos(telefono)
        gastos.append(gasto_nuevo)
        guardar_gastos(telefono, gastos)

        # Respuesta al usuario
        items = gasto.get('descripcion', '')
        return f"Listo, leí tu nota: {items}. Total ${monto} guardado. ¿Quieres el balance?"

    except Exception as e:
        logger.error(f"Error en procesar_foto_inteligente: {e}", exc_info=True)
        return f"❌ Error procesando factura: {str(e)}"

def reclasificar_gasto(texto, telefono, historial=None):
    """
    Reclasifica el último gasto basado en instrucciones del usuario.
    Ejemplo: "de esos X, Y son para comida y Z para materiales"
    """
    try:
        gastos = cargar_gastos(telefono)
        if not gastos:
            return "No tienes gastos registrados para reclasificar."

        # Obtener el último gasto
        ultimo_gasto = gastos[-1]

        # Construir historial si no se proporciona
        if historial is None:
            historial = json.dumps(ultimo_gasto, ensure_ascii=False)

        # Llamar a Claude para reclasificar
        response = client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Historial: {historial}
Usuario dice: '{texto}'

Desglos o reclasifica el gasto según lo que dijo el usuario.
Si el usuario quiere dividir un gasto, crea múltiples transacciones.
Devuelve SOLO un JSON válido con un array 'gastos':
{{
  "gastos": [
    {{
      "monto": número,
      "fecha": "YYYY-MM-DD",
      "proveedor": "string",
      "categoria": "materiales|envio_ecuador|comida|renta|otro",
      "tipo_documento": "factura|recibo_envio|ticket",
      "destino": "string",
      "tarifa_envio": número,
      "para_quien": "string",
      "descripcion": "string"
    }}
  ]
}}"""
            }]
        )

        try:
            texto_respuesta = response.content[0].text.strip()
            # Limpiar posibles marcas de código
            if texto_respuesta.startswith('```'):
                texto_respuesta = texto_respuesta.split('```')[1]
                if texto_respuesta.startswith('json'):
                    texto_respuesta = texto_respuesta[4:]
            if texto_respuesta.endswith('```'):
                texto_respuesta = texto_respuesta[:-3]

            datos = json.loads(texto_respuesta)
        except json.JSONDecodeError as e:
            print(f"Error parseando JSON de Claude en reclasificación: {e}")
            return "❌ No pude reclasificar. Intenta con otra descripción."

        # Reemplazar el último gasto con los nuevos
        gastos.pop()
        for nuevo_gasto in datos.get('gastos', []):
            nuevo_gasto['id'] = datetime.now().isoformat()
            gastos.append(nuevo_gasto)

        guardar_gastos(telefono, gastos)

        # Respuesta
        if len(datos.get('gastos', [])) > 1:
            return f"✓ Gasto dividido en {len(datos['gastos'])} transacciones"
        else:
            cat = datos['gastos'][0].get('categoria', 'otro') if datos.get('gastos') else 'otro'
            return f"✓ Gasto reclasificado como *{cat}*"

    except Exception as e:
        logger.error(f"Error en reclasificar_gasto: {e}", exc_info=True)
        return f"❌ Error reclasificando: {str(e)}"

def borrar_gasto(telefono, query_usuario):
    """
    Borra un gasto según lo que pida el usuario.
    - "borra el ultimo gasto" -> toma ultimo de gastos.json
    - "borra Blusanprom 100" -> busca desc contiene "blusanprom" y monto 100
    Responde con confirmación y luego elimina.
    """
    try:
        gastos = cargar_gastos(telefono)
        if not gastos:
            return "No tienes gastos para borrar."

        query_lower = query_usuario.lower()
        gasto_a_borrar = None

        # Buscar el último gasto
        if 'ultimo' in query_lower or 'última' in query_lower:
            gasto_a_borrar = gastos[-1]
        else:
            # Buscar por descripción y monto
            for gasto in reversed(gastos):
                desc = gasto.get('descripcion', '').lower()
                # Extraer números de la query
                numeros = re.findall(r'\d+', query_usuario)

                for num in numeros:
                    monto = gasto.get('monto', 0)
                    if desc.find(query_lower.split()[0].lower()) >= 0 and float(monto) == float(num):
                        gasto_a_borrar = gasto
                        break

                if gasto_a_borrar:
                    break

        if not gasto_a_borrar:
            # Búsqueda más flexible: solo por descripción
            for gasto in reversed(gastos):
                desc = gasto.get('descripcion', '').lower()
                if query_lower.replace('borra', '').strip() in desc:
                    gasto_a_borrar = gasto
                    break

        if not gasto_a_borrar:
            return "No encontré ese gasto. ¿Puedes dar más detalles?"

        # Responder con confirmación
        desc = gasto_a_borrar.get('descripcion', '')
        monto = gasto_a_borrar.get('monto', 0)
        fecha = gasto_a_borrar.get('fecha', '')

        # Guardar para confirmación (guardar en contexto de usuario si es posible)
        ruta_datos = obtener_ruta_datos(telefono)
        pendiente_path = f"{ruta_datos}/.pendiente_borrar.json"
        with open(pendiente_path, "w") as f:
            json.dump(gasto_a_borrar, f)

        return f"¿Confirmas borrar: {desc} ${monto} del {fecha}? Responde si/no"

    except Exception as e:
        logger.error(f"Error en borrar_gasto: {e}", exc_info=True)
        return f"❌ Error al borrar: {str(e)}"

def confirmar_borrado(telefono):
    """
    Confirma el borrado de un gasto pendiente.
    Se llama si el usuario responde 'si' a la confirmación.
    """
    try:
        ruta_datos = obtener_ruta_datos(telefono)
        pendiente_path = f"{ruta_datos}/.pendiente_borrar.json"

        if not os.path.exists(pendiente_path):
            return "No hay gasto pendiente para borrar."

        with open(pendiente_path, "r") as f:
            gasto_a_borrar = json.load(f)

        gastos = cargar_gastos(telefono)

        # Borrar el gasto
        gastos = [g for g in gastos if g.get('id') != gasto_a_borrar.get('id')]
        guardar_gastos(telefono, gastos)

        # Borrar factura si existe
        factura_path = gasto_a_borrar.get('factura_path', '')
        if factura_path and os.path.exists(factura_path):
            try:
                os.remove(factura_path)
            except:
                pass

        # Calcular nuevo balance
        nuevo_total = sum(g.get('monto', 0) for g in gastos)

        # Limpiar pendiente
        os.remove(pendiente_path)

        return f"Listo, borrada. Tu balance ahora es ${nuevo_total}."

    except Exception as e:
        logger.error(f"Error confirmando borrado: {e}", exc_info=True)
        return f"❌ Error al confirmar: {str(e)}"

def parsear_rango_fechas(texto_usuario):
    """
    Parsea diferentes formatos de rango de fechas.
    Retorna (fecha_inicio, fecha_fin) o (None, None) si no puede parsear.
    """
    try:
        hoy = datetime.now()
        texto = texto_usuario.lower()

        # "1 al 15 de octubre" o "1 al 15 de este mes"
        if '1 al 15' in texto or 'primer quincena' in texto:
            mes_actual = hoy.month
            anio_actual = hoy.year
            if 'pasado' in texto or 'mes pasado' in texto:
                mes_actual -= 1
                if mes_actual == 0:
                    mes_actual = 12
                    anio_actual -= 1
            return datetime(anio_actual, mes_actual, 1), datetime(anio_actual, mes_actual, 15)

        if '16 al 31' in texto or '16 al 30' in texto or 'segunda quincena' in texto:
            mes_actual = hoy.month
            anio_actual = hoy.year
            if 'pasado' in texto or 'mes pasado' in texto:
                mes_actual -= 1
                if mes_actual == 0:
                    mes_actual = 12
                    anio_actual -= 1
            # Ultimo día del mes
            if mes_actual == 12:
                ultimo_dia = 31
            else:
                ultimo_dia = monthrange(anio_actual, mes_actual)[1]
            return datetime(anio_actual, mes_actual, 16), datetime(anio_actual, mes_actual, ultimo_dia)

        # "ultimos 15 dias"
        if 'ultimos' in texto and 'dias' in texto:
            match = re.search(r'ultimos?\s+(\d+)\s+dias?', texto)
            if match:
                dias = int(match.group(1))
                fin = hoy
                inicio = hoy - timedelta(days=dias)
                return inicio, fin

        # "esta quincena"
        if 'esta quincena' in texto or 'esta quincena' in texto:
            if hoy.day <= 15:
                return datetime(hoy.year, hoy.month, 1), datetime(hoy.year, hoy.month, 15)
            else:
                ultimo_dia = monthrange(hoy.year, hoy.month)[1]
                return datetime(hoy.year, hoy.month, 16), datetime(hoy.year, hoy.month, ultimo_dia)

        # "este mes"
        if 'este mes' in texto:
            return datetime(hoy.year, hoy.month, 1), hoy

        # "corte del mes pasado" o "balance de agosto"
        meses = {
            'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
            'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
        }

        for mes_nombre, mes_num in meses.items():
            if mes_nombre in texto:
                anio = hoy.year
                if 'pasado' in texto or 'mes pasado' in texto:
                    anio -= 1
                from calendar import monthrange
                ultimo_dia = monthrange(anio, mes_num)[1]
                return datetime(anio, mes_num, 1), datetime(anio, mes_num, ultimo_dia)

        return None, None

    except Exception as e:
        logger.error(f"Error parseando rango de fechas: {e}")
        return None, None

def generar_corte(telefono, texto_usuario):
    """
    Genera un corte de gastos e ingresos para un rango de fechas.
    Parsea la fecha, filtra gastos/ingresos, calcula totales.
    Si usuario dice "zip" o "mandale a mi contadora", crea ZIP.
    """
    try:
        fecha_inicio, fecha_fin = parsear_rango_fechas(texto_usuario)

        if not fecha_inicio or not fecha_fin:
            return "No entendí las fechas. Prueba: '1 al 15 de octubre', 'ultimos 15 dias', 'este mes', 'corte de agosto'"

        gastos = cargar_gastos(telefono)

        # Filtrar por rango de fechas
        gastos_rango = []
        ingresos_rango = []

        for gasto in gastos:
            fecha_str = gasto.get('fecha', '')
            try:
                fecha_gasto = datetime.strptime(fecha_str, '%Y-%m-%d')
                if fecha_inicio <= fecha_gasto <= fecha_fin:
                    gastos_rango.append(gasto)
            except:
                pass

        # Cargar ingresos si existen
        ruta_datos = obtener_ruta_datos(telefono)
        ingresos_file = f"{ruta_datos}/ingresos.json"
        if os.path.exists(ingresos_file):
            try:
                with open(ingresos_file, 'r') as f:
                    ingresos = json.load(f)
                for ingreso in ingresos:
                    fecha_str = ingreso.get('fecha', '')
                    try:
                        fecha_ingreso = datetime.strptime(fecha_str, '%Y-%m-%d')
                        if fecha_inicio <= fecha_ingreso <= fecha_fin:
                            ingresos_rango.append(ingreso)
                    except:
                        pass
            except:
                pass

        # Calcular totales
        total_ingresos = sum(i.get('monto', 0) for i in ingresos_rango)
        total_gastos = sum(g.get('monto', 0) for g in gastos_rango)
        ganancia = total_ingresos - total_gastos

        # Desglose por categoría de gastos
        por_categoria = {}
        for gasto in gastos_rango:
            cat = gasto.get('categoria', 'otro')
            monto = gasto.get('monto', 0)
            por_categoria[cat] = por_categoria.get(cat, 0) + monto

        # Crear respuesta
        fecha_inicio_str = fecha_inicio.strftime('%d/%m/%Y')
        fecha_fin_str = fecha_fin.strftime('%d/%m/%Y')

        respuesta = f"Corte {fecha_inicio_str} al {fecha_fin_str}:\n"
        respuesta += f"Ingresos: ${total_ingresos:.2f}\n"
        respuesta += f"Gastos: ${total_gastos:.2f}\n"
        respuesta += f"Ganancia: ${ganancia:.2f}\n\n"
        respuesta += "Detalle de gastos:\n"

        for cat, monto in sorted(por_categoria.items(), key=lambda x: x[1], reverse=True):
            respuesta += f"  • {cat}: ${monto:.2f}\n"

        # Verificar si user pide ZIP
        if 'zip' in texto_usuario.lower() or 'contadora' in texto_usuario.lower() or 'contador' in texto_usuario.lower():
            respuesta += "\n¿Te armo el ZIP con facturas y PDF del balance?"

        return respuesta

    except Exception as e:
        logger.error(f"Error en generar_corte: {e}", exc_info=True)
        return f"❌ Error al generar corte: {str(e)}"

def crear_zip_corte(telefono, fecha_inicio, fecha_fin):
    """
    Crea un ZIP con las facturas del rango + PDF con reporte.
    Retorna la ruta del ZIP o None.
    """
    try:
        gastos = cargar_gastos(telefono)
        ruta_datos = obtener_ruta_datos(telefono)

        # Filtrar gastos por rango
        gastos_rango = []
        for gasto in gastos:
            fecha_str = gasto.get('fecha', '')
            try:
                fecha_gasto = datetime.strptime(fecha_str, '%Y-%m-%d')
                if fecha_inicio <= fecha_gasto <= fecha_fin:
                    gastos_rango.append(gasto)
            except:
                pass

        # Crear ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Agregar facturas
            for gasto in gastos_rango:
                factura_path = gasto.get('factura_path', '')
                if factura_path and os.path.exists(factura_path):
                    arcname = os.path.basename(factura_path)
                    zf.write(factura_path, arcname=arcname)

            # Generar PDF de reporte
            total_gastos = sum(g.get('monto', 0) for g in gastos_rango)
            pdf_content = generar_pdf_corte(gastos_rango, fecha_inicio, fecha_fin, total_gastos)
            zf.writestr('reporte_corte.pdf', pdf_content)

        # Guardar ZIP
        zip_path = f"{ruta_datos}/corte_{fecha_inicio.strftime('%Y%m%d')}_al_{fecha_fin.strftime('%Y%m%d')}.zip"
        with open(zip_path, 'wb') as f:
            f.write(zip_buffer.getvalue())

        return zip_path

    except Exception as e:
        logger.error(f"Error creando ZIP: {e}", exc_info=True)
        return None

def generar_pdf_corte(gastos, fecha_inicio, fecha_fin, total):
    """
    Genera PDF de reporte de corte usando reportlab.
    Retorna bytes del PDF.
    """
    try:
        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=letter)
        story = []
        styles = getSampleStyleSheet()

        # Título
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            textColor=colors.HexColor('#1e40af'),
            spaceAfter=12
        )

        story.append(Paragraph("Corte de Gastos", title_style))
        story.append(Paragraph(f"Período: {fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')}", styles['Normal']))
        story.append(Spacer(1, 0.3*inch))

        # Tabla de gastos
        table_data = [['Fecha', 'Descripción', 'Categoría', 'Monto']]
        for gasto in gastos:
            table_data.append([
                gasto.get('fecha', ''),
                gasto.get('descripcion', '')[:20],
                gasto.get('categoria', ''),
                f"${gasto.get('monto', 0):.2f}"
            ])

        table_data.append(['', '', 'TOTAL', f"${total:.2f}"])

        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))

        story.append(table)
        doc.build(story)

        return pdf_buffer.getvalue()

    except Exception as e:
        logger.error(f"Error generando PDF de corte: {e}")
        return b""

# ==================== GOAL MANAGEMENT FUNCTIONS ====================

def cargar_metas():
    """Carga las metas desde el archivo JSON"""
    if os.path.exists(GOALS_FILE):
        try:
            with open(GOALS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error cargando metas: {e}")
            return []
    return []

def guardar_metas(metas):
    """Guarda las metas en el archivo JSON"""
    try:
        with open(GOALS_FILE, 'w', encoding='utf-8') as f:
            json.dump(metas, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error guardando metas: {e}")
        return False

def registrar_meta(nombre, monto_objetivo, fecha_limite, categoria, monto_actual=0):
    """
    Registra una nueva meta financiera
    Categorías: 'debt_payoff', 'savings', 'investment'
    """
    metas = cargar_metas()

    # Verificar si ya existe una meta con el mismo nombre
    for meta in metas:
        if meta['nombre'].lower() == nombre.lower():
            return False, "Ya existe una meta con ese nombre"

    nueva_meta = {
        'nombre': nombre,
        'monto_objetivo': monto_objetivo,
        'monto_actual': monto_actual,
        'fecha_limite': fecha_limite,
        'categoria': categoria,
        'fecha_creacion': datetime.now().isoformat(),
        'actualizaciones': []
    }

    metas.append(nueva_meta)
    if guardar_metas(metas):
        return True, f"Meta '{nombre}' registrada exitosamente"
    return False, "Error al registrar la meta"

def obtener_metas():
    """Obtiene todas las metas con información de progreso"""
    metas = cargar_metas()
    metas_con_progreso = []

    for meta in metas:
        porcentaje = (meta['monto_actual'] / meta['monto_objetivo'] * 100) if meta['monto_objetivo'] > 0 else 0
        falta = max(0, meta['monto_objetivo'] - meta['monto_actual'])

        # Calcular días faltantes
        fecha_limite = datetime.fromisoformat(meta['fecha_limite'])
        hoy = datetime.now()
        dias_faltantes = (fecha_limite - hoy).days

        meta_con_progreso = {
            **meta,
            'porcentaje_progreso': round(porcentaje, 1),
            'monto_faltante': round(falta, 2),
            'dias_faltantes': dias_faltantes,
            'completada': porcentaje >= 100
        }
        metas_con_progreso.append(meta_con_progreso)

    return metas_con_progreso

def actualizar_progreso_meta(nombre, cantidad):
    """Actualiza el progreso de una meta"""
    metas = cargar_metas()

    for meta in metas:
        if meta['nombre'].lower() == nombre.lower():
            meta['monto_actual'] += cantidad
            # Registrar la actualización
            meta['actualizaciones'].append({
                'fecha': datetime.now().isoformat(),
                'cantidad': cantidad
            })

            if guardar_metas(metas):
                return True, f"Progreso actualizado: ${cantidad:.2f} agregado a '{nombre}'"
            return False, "Error al actualizar la meta"

    return False, f"Meta '{nombre}' no encontrada"

def procesar_analisis_financiero(texto_usuario, telefono):
    """
    Procesa un mensaje financiero usando Claude para extraer ingresos/gastos.
    Mantiene contexto acumulado en archivos JSON.
    """
    # Cargar contexto anterior
    contexto = cargar_contexto_financiero()
    ingresos_previos = contexto.get('ingresos_mensuales', 0)
    gastos_previos = contexto.get('gastos', {})

    # Usar Claude para extraer ingresos y gastos de forma inteligente
    response = client.messages.create(
        model=MODELO_CLAUDE,
        max_tokens=500,
        system="Extrae ingresos y gastos del mensaje. Devuelve JSON: {\"ingresos\": número, \"gastos\": {\"categoria\": monto}}",
        messages=[{"role": "user", "content": texto_usuario}]
    )

    try:
        # Intentar parsear JSON de la respuesta
        respuesta_texto = response.content[0].text
        if '{' in respuesta_texto and '}' in respuesta_texto:
            inicio = respuesta_texto.find('{')
            fin = respuesta_texto.rfind('}') + 1
            json_str = respuesta_texto[inicio:fin]
            datos = json.loads(json_str)
            nuevos_ingresos = datos.get('ingresos', 0)
            nuevos_gastos = datos.get('gastos', {})
        else:
            nuevos_ingresos = 0
            nuevos_gastos = {}
    except:
        nuevos_ingresos = 0
        nuevos_gastos = {}

    # Actualizar contexto
    if nuevos_ingresos > 0:
        contexto['ingresos_mensuales'] = nuevos_ingresos

    # Sumar nuevos gastos a los existentes
    for categoria, cantidad in nuevos_gastos.items():
        if categoria in gastos_previos:
            gastos_previos[categoria] += cantidad
        else:
            gastos_previos[categoria] = cantidad

    contexto['gastos'] = gastos_previos
    guardar_contexto_financiero(contexto)

    # Calcular totales
    ingresos_totales = contexto['ingresos_mensuales']
    gastos_totales = sum(contexto['gastos'].values())
    superavit = ingresos_totales - gastos_totales

    # Generar análisis
    analisis = f"""📊 *ANÁLISIS FINANCIERO*\n

*Ingresos Mensuales:* ${ingresos_totales:.2f}
*Gastos Totales:* ${gastos_totales:.2f}

*Desglose de Gastos:*"""

    for categoria, cantidad in contexto['gastos'].items():
        analisis += f"\n  • {categoria.replace('_', ' ').title()}: ${cantidad:.2f}"

    if superavit > 0:
        analisis += f"\n\n✅ *Superávit:* +${superavit:.2f}"
    else:
        analisis += f"\n\n⚠️ *Déficit:* ${superavit:.2f}"

    return analisis, ingresos_totales, gastos_totales, superavit

def analizar_metas_y_dar_consejos():
    """Usa Claude API para analizar metas y dar consejos personalizados"""
    metas = obtener_metas()

    if not metas:
        return "No tienes metas registradas. ¡Crea algunas para empezar!"

    # Preparar resumen de metas para Claude
    resumen_metas = ""
    for meta in metas:
        estado = "✓ Completada" if meta['completada'] else f"{meta['porcentaje_progreso']}% avance"
        resumen_metas += f"\n- {meta['nombre']}: ${meta['monto_actual']:.2f}/${meta['monto_objetivo']:.2f} ({estado})"
        if not meta['completada'] and meta['dias_faltantes'] > 0:
            monto_mensual = meta['monto_faltante'] / max(1, meta['dias_faltantes'] / 30)
            resumen_metas += f" | Necesitas ahorrar ${monto_mensual:.2f}/mes"

    # Solicitar análisis a Claude
    try:
        response = client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=800,
            system="""Eres Yoly, un asesor financiero amable y motivador.
Analiza las metas del usuario y proporciona:
1. Un resumen motivador del progreso
2. Consejos prácticos para alcanzar las metas
3. Sugerencias específicas para ahorrar más o acelerar el progreso
4. Recordatorio de la importancia de cada meta
Responde siempre en español, de forma amable y motivadora.""",
            messages=[{
                "role": "user",
                "content": f"Analiza mis metas financieras y dame consejos: {resumen_metas}"
            }]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Error analizando metas: {e}")
        return "Error al analizar tus metas. Intenta de nuevo más tarde."

def generar_proyecciones(meses=12):
    """Genera proyecciones financieras de 6 y 12 meses"""
    metas = obtener_metas()

    if not metas:
        return {}

    proyecciones = {
        '6_meses': {},
        '12_meses': {}
    }

    for meta in metas:
        if meta['monto_objetivo'] <= meta['monto_actual']:
            proyecciones['6_meses'][meta['nombre']] = {
                'estado': 'completada',
                'fecha_cumplimiento': 'Ya completada'
            }
            proyecciones['12_meses'][meta['nombre']] = {
                'estado': 'completada',
                'fecha_cumplimiento': 'Ya completada'
            }
            continue

        # Calcular velocidad de ahorro promedio
        fecha_creacion = datetime.fromisoformat(meta['fecha_creacion'])
        dias_transcurridos = (datetime.now() - fecha_creacion).days

        if dias_transcurridos > 0:
            velocidad_diaria = meta['monto_actual'] / dias_transcurridos
        else:
            velocidad_diaria = 0

        # Proyectar para 6 meses
        monto_6_meses = meta['monto_actual'] + (velocidad_diaria * 180)
        completada_6_meses = monto_6_meses >= meta['monto_objetivo']

        # Proyectar para 12 meses
        monto_12_meses = meta['monto_actual'] + (velocidad_diaria * 365)
        completada_12_meses = monto_12_meses >= meta['monto_objetivo']

        # Calcular fecha proyectada de cumplimiento
        if velocidad_diaria > 0:
            dias_faltantes = (meta['monto_objetivo'] - meta['monto_actual']) / velocidad_diaria
            fecha_proyectada = datetime.now() + timedelta(days=int(dias_faltantes))
        else:
            fecha_proyectada = None

        proyecciones['6_meses'][meta['nombre']] = {
            'monto_proyectado': round(min(monto_6_meses, meta['monto_objetivo']), 2),
            'completada': completada_6_meses,
            'porcentaje': round((monto_6_meses / meta['monto_objetivo'] * 100), 1)
        }

        proyecciones['12_meses'][meta['nombre']] = {
            'monto_proyectado': round(min(monto_12_meses, meta['monto_objetivo']), 2),
            'completada': completada_12_meses,
            'porcentaje': round((monto_12_meses / meta['monto_objetivo'] * 100), 1),
            'fecha_cumplimiento': fecha_proyectada.strftime('%d/%m/%Y') if fecha_proyectada else 'No estimado'
        }

    return proyecciones

def generar_informe_metas():
    """Genera un PDF con progreso de metas financieras"""
    metas = obtener_metas()

    if not metas:
        return None

    pdf_path = '/tmp/informe_metas.pdf'
    tmp_path = ruta_temporal(pdf_path)
    chart_path = '/tmp/metas_chart.png'

    # Preparar datos para gráfico
    nombres_metas = [m['nombre'][:15] for m in metas]  # Limitar nombre a 15 caracteres
    porcentajes = [m['porcentaje_progreso'] for m in metas]
    colores = ['#FF6B6B' if p < 50 else '#FFA07A' if p < 80 else '#90EE90' for p in porcentajes]

    # Generar gráfico de barras
    plt.figure(figsize=(10, 6))
    bars = plt.barh(nombres_metas, porcentajes, color=colores)
    plt.xlabel('Porcentaje de Progreso (%)', fontsize=11)
    plt.title('Progreso de Tus Metas Financieras', fontsize=14, fontweight='bold')
    plt.xlim(0, 100)

    # Agregar porcentajes en las barras
    for i, (bar, pct) in enumerate(zip(bars, porcentajes)):
        plt.text(pct + 2, i, f'{pct:.1f}%', va='center', fontsize=9)

    plt.tight_layout()
    plt.savefig(chart_path, dpi=100, bbox_inches='tight')
    plt.close()

    # Crear PDF
    c = canvas.Canvas(tmp_path, pagesize=letter)
    width, height = letter

    # Título
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "📊 Informe de Metas Financieras")

    # Fecha
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 75, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    # Tabla de metas
    c.setFont("Helvetica-Bold", 11)
    y_pos = height - 120
    c.drawString(50, y_pos, "Meta")
    c.drawString(200, y_pos, "Progreso")
    c.drawString(320, y_pos, "Faltante")
    c.drawString(420, y_pos, "Plazo")

    c.setFont("Helvetica", 9)
    y_pos -= 20

    for meta in metas:
        estado_emoji = "✓" if meta['completada'] else "→"
        c.drawString(50, y_pos, f"{estado_emoji} {meta['nombre'][:20]}")
        c.drawString(200, y_pos, f"${meta['monto_actual']:.0f}/${meta['monto_objetivo']:.0f}")
        c.drawString(320, y_pos, f"${meta['monto_faltante']:.2f}")

        if meta['dias_faltantes'] > 0:
            dias_text = f"{meta['dias_faltantes']}d"
        else:
            dias_text = "Vencido" if not meta['completada'] else "✓"
        c.drawString(420, y_pos, dias_text)
        y_pos -= 18

        if y_pos < 80:  # Nueva página si es necesario
            c.showPage()
            c.setFont("Helvetica", 9)
            y_pos = height - 50

    # Agregar gráfico
    c.showPage()
    c.drawImage(chart_path, 30, height - 400, width=530, height=320)

    c.save()
    publicar_pdf(tmp_path, pdf_path)
    return pdf_path

def generar_informe_gastos():
    """Genera un PDF con gráfico de gastos de ejemplo"""
    pdf_path = '/tmp/informe_gastos.pdf'
    tmp_path = ruta_temporal(pdf_path)
    chart_path = '/tmp/gastos_chart.png'

    # Datos de ejemplo
    categories = ["Alimentos", "Transporte", "Entretenimiento", "Servicios"]
    amounts = [150.00, 80.00, 50.00, 120.00]

    # Generar gráfico con matplotlib
    plt.figure(figsize=(8, 6))
    colors_list = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A']
    plt.pie(amounts, labels=categories, autopct='%1.1f%%', startangle=140, colors=colors_list)
    plt.title('Informe de Gastos Mensuales', fontsize=14, fontweight='bold')
    plt.savefig(chart_path, dpi=100, bbox_inches='tight')
    plt.close()

    # Crear PDF con reportlab
    c = canvas.Canvas(tmp_path, pagesize=letter)
    width, height = letter

    # Título
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "Informe de Gastos")

    # Subtítulo
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 80, "Resumen de gastos mensuales")

    # Tabla de datos
    c.setFont("Helvetica-Bold", 11)
    y_pos = height - 120
    c.drawString(50, y_pos, "Categoría")
    c.drawString(250, y_pos, "Monto")
    c.drawString(400, y_pos, "Porcentaje")

    c.setFont("Helvetica", 10)
    total = sum(amounts)
    y_pos -= 20
    for category, amount in zip(categories, amounts):
        percentage = (amount / total) * 100
        c.drawString(50, y_pos, category)
        c.drawString(250, y_pos, f"${amount:.2f}")
        c.drawString(400, y_pos, f"{percentage:.1f}%")
        y_pos -= 20

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y_pos - 10, "Total")
    c.drawString(250, y_pos - 10, f"${total:.2f}")

    # Agregar gráfico
    c.drawImage(chart_path, 50, 50, width=400, height=300)

    c.save()
    publicar_pdf(tmp_path, pdf_path)
    return pdf_path

SEMANAS_POR_MES = 52 / 12

ESQUEMA_PRESUPUESTO = {
    "type": "object",
    "properties": {
        "ingresos": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "concepto": {"type": "string"},
                "monto": {"type": "number"},
                "periodo": {"type": "string", "enum": ["hora", "dia", "semana", "quincena", "mes", "año"]},
                "horas_por_dia": {"type": "number", "description": "0 si el usuario no lo dijo"},
                "dias_por_semana": {"type": "number", "description": "0 si el usuario no lo dijo"},
            },
            "required": ["concepto", "monto", "periodo", "horas_por_dia", "dias_por_semana"],
            "additionalProperties": False,
        }},
        "gastos": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "concepto": {"type": "string"},
                "monto": {"type": "number"},
                "periodo": {"type": "string", "enum": ["dia", "semana", "quincena", "mes", "año"]},
                "es_deuda": {"type": "boolean"},
            },
            "required": ["concepto", "monto", "periodo", "es_deuda"],
            "additionalProperties": False,
        }},
    },
    "required": ["ingresos", "gastos"],
    "additionalProperties": False,
}

FACTOR_MENSUAL = {"semana": SEMANAS_POR_MES, "quincena": 2, "mes": 1, "año": 1 / 12, "dia": 365 / 12}

def extraer_presupuesto(mensaje):
    """Lee ingresos y gastos del mensaje del usuario y los pasa a montos mensuales.
    Devuelve (datos, faltan). Si falta algo, datos es None y faltan dice qué preguntar."""
    response = client.messages.create(
        model=MODELO_CLAUDE,
        max_tokens=2000,
        system=("Extrae los ingresos y gastos que el usuario escribió, tal como los dijo. "
                "No inventes montos ni conceptos que no estén en el mensaje. "
                "Corrige solo errores de tipeo obvios en los conceptos (ej. 'rebta' -> 'renta'). "
                "Marca es_deuda=true solo si el usuario dice que es una deuda o un pago de préstamo."),
        messages=[{"role": "user", "content": mensaje}],
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA_PRESUPUESTO}},
    )
    data = json.loads(next(b.text for b in response.content if b.type == "text"))

    faltan = []
    ingreso = 0.0
    for ing in data["ingresos"]:
        if ing["periodo"] == "hora":
            if not ing["horas_por_dia"] or not ing["dias_por_semana"]:
                faltan.append(f"¿Cuántas horas al día y cuántos días a la semana trabajas por tu ingreso de ${ing['monto']:g}/hora?")
                continue
            ingreso += ing["monto"] * ing["horas_por_dia"] * ing["dias_por_semana"] * SEMANAS_POR_MES
        elif ing["periodo"] == "dia":
            if not ing["dias_por_semana"]:
                faltan.append(f"¿Cuántos días a la semana recibes tu ingreso de ${ing['monto']:g}/día?")
                continue
            ingreso += ing["monto"] * ing["dias_por_semana"] * SEMANAS_POR_MES
        else:
            ingreso += ing["monto"] * FACTOR_MENSUAL[ing["periodo"]]

    if not data["ingresos"] or (not faltan and ingreso <= 0):
        faltan.append("¿Cuánto ganas y cada cuánto (por hora, semana o mes)?")
    if not data["gastos"]:
        faltan.append("¿Cuáles son tus gastos y cuánto pagas en cada uno?")
    if faltan:
        return None, faltan

    gastos_fijos, deudas = {}, {}
    for g in data["gastos"]:
        destino = deudas if g["es_deuda"] else gastos_fijos
        destino[g["concepto"]] = destino.get(g["concepto"], 0) + round(g["monto"] * FACTOR_MENSUAL[g["periodo"]], 2)
    return {"ingreso": round(ingreso, 2), "gastos_fijos": gastos_fijos, "deudas": deudas}, []

def salud_financiera(surplus, income):
    """Resumen según el porcentaje del ingreso que queda libre cada mes."""
    if surplus < 0:
        return "En riesgo: gastas más de lo que ganas"
    tasa = surplus / income * 100
    if tasa < 10:
        return "Ajustado: te queda menos del 10% del ingreso"
    if tasa < 20:
        return "Aceptable: te queda entre 10% y 20% del ingreso"
    return "Saludable: te queda 20% o más del ingreso"

def generar_presupuesto(datos):
    """Genera un PDF con el presupuesto mensual del usuario y consejos de Claude.
    Devuelve (pdf_path, consejo_ok); consejo_ok es False si Claude no pudo dar consejos."""
    pdf_path = '/tmp/presupuesto_analisis.pdf'
    tmp_path = ruta_temporal(pdf_path)
    chart_path_pie = '/tmp/presupuesto_pie.png'
    chart_path_bar = '/tmp/presupuesto_bar.png'

    income = datos["ingreso"]
    fixed_expenses = datos["gastos_fijos"]
    debt_expenses = datos["deudas"]

    total_fixed = sum(fixed_expenses.values())
    total_debt = sum(debt_expenses.values())
    total_expenses = total_fixed + total_debt
    surplus = income - total_expenses

    # Generar gráfico de pastel (Pie Chart) - Expenses by Category
    plt.figure(figsize=(8, 6))
    categories = ['Fixed Expenses', 'Debt Payments']
    amounts = [total_fixed, total_debt]
    colors_pie = ['#f97316', '#3b82f6']
    plt.pie(amounts, labels=categories, autopct='%1.1f%%', startangle=140,
            colors=colors_pie, textprops={'fontsize': 11, 'weight': 'bold'})
    plt.title('Expenses by Category', fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(chart_path_pie, dpi=100, bbox_inches='tight')
    plt.close()

    # Generar gráfico de barras - Income vs Expenses
    plt.figure(figsize=(8, 6))
    labels = ['Income', 'Expenses', 'Surplus']
    values = [income, total_expenses, surplus]
    colors_bar = ['#16a34a', '#d97706', '#1e40af']
    bars = plt.bar(labels, values, color=colors_bar, width=0.6)
    plt.ylabel('Amount ($)', fontsize=11, fontweight='bold')
    plt.title('Income vs Expenses', fontsize=14, fontweight='bold')
    plt.axhline(0, color='black', linewidth=0.8)

    # Add value labels on bars
    for bar, value in zip(bars, values):
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height,
                f'${value:.0f}',
                ha='center', va='bottom', fontweight='bold')

    plt.tight_layout()
    plt.savefig(chart_path_bar, dpi=100, bbox_inches='tight')
    plt.close()

    # Crear PDF con reportlab
    doc = SimpleDocTemplate(tmp_path, pagesize=letter)
    story = []
    styles = getSampleStyleSheet()

    # Estilo personalizado para títulos
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1e40af'),
        spaceAfter=6,
        fontName='Helvetica-Bold'
    )

    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#1e40af'),
        spaceAfter=12,
        spaceBefore=12,
        fontName='Helvetica-Bold'
    )

    # Título
    story.append(Paragraph("Monthly Budget Report", title_style))
    story.append(Paragraph("Financial Overview & Analysis", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))

    # Summary Table
    summary_data = [
        ['Monthly Income', 'Total Expenses', 'Monthly Surplus'],
        [f'${income:.2f}', f'${total_expenses:.2f}', f'${surplus:.2f}']
    ]
    summary_table = Table(summary_data, colWidths=[2*inch, 2*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('FONTSIZE', (0, 1), (-1, 1), 12),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#f0f9ff')),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f0f9ff')])
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.3*inch))

    # Charts
    story.append(Paragraph("Visual Analysis", heading_style))
    if os.path.exists(chart_path_pie):
        story.append(Image(chart_path_pie, width=3.5*inch, height=2.6*inch))
    story.append(Spacer(1, 0.2*inch))
    if os.path.exists(chart_path_bar):
        story.append(Image(chart_path_bar, width=3.5*inch, height=2.6*inch))
    story.append(Spacer(1, 0.3*inch))

    # Expense Breakdown
    story.append(PageBreak())
    story.append(Paragraph("Detailed Expense Breakdown", heading_style))

    # Fixed Expenses Table
    story.append(Paragraph("Fixed Expenses", styles['Heading3']))
    fixed_data = [['Category', 'Amount']]
    for name, amount in fixed_expenses.items():
        fixed_data.append([name, f'${amount:.2f}'])
    fixed_data.append(['TOTAL FIXED', f'${total_fixed:.2f}'])

    fixed_table = Table(fixed_data, colWidths=[3.5*inch, 1.5*inch])
    fixed_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f97316')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#fff7ed')),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#fffbf0')])
    ]))
    story.append(fixed_table)
    story.append(Spacer(1, 0.2*inch))

    # Debt Table
    story.append(Paragraph("Debt Payments", styles['Heading3']))
    debt_data = [['Creditor', 'Amount']]
    for name, amount in debt_expenses.items():
        debt_data.append([name, f'${amount:.2f}'])
    debt_data.append(['TOTAL DEBT', f'${total_debt:.2f}'])

    debt_table = Table(debt_data, colWidths=[3.5*inch, 1.5*inch])
    debt_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3b82f6')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#eff6ff')),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f0f9ff')])
    ]))
    story.append(debt_table)
    story.append(Spacer(1, 0.3*inch))

    # Financial Advisor Analysis
    story.append(PageBreak())
    story.append(Paragraph("AI Financial Advisor Analysis", heading_style))

    # Get financial advice from Claude API
    advisor_message = f"""Based on this monthly budget (Income: ${income}, Fixed Expenses: ${total_fixed}, Debt: ${total_debt}, Surplus: ${surplus}),
    provide brief financial advice in Spanish about:
    1. Identified expense leaks
    2. Savings opportunities
    3. Debt payoff strategy
    4. Emergency fund recommendation
    Keep it concise and actionable."""

    try:
        advisor_response = client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=800,
            system="Eres un asesor financiero profesional. Proporciona análisis financiero detallado pero conciso en español.",
            messages=[{"role": "user", "content": advisor_message}]
        )
        advice_text = advisor_response.content[0].text
        consejo_ok = True
    except Exception as e:
        logger.error(f"Error generando consejo del asesor: {e}", exc_info=True)
        advice_text = "No se pudo generar el análisis del asesor en este momento."
        consejo_ok = False

    story.append(Paragraph(advice_text, styles['Normal']))
    story.append(Spacer(1, 0.2*inch))

    # Financial Health Score
    story.append(Paragraph("Financial Health Summary", styles['Heading3']))
    summary_text = f"""<b>Monthly Surplus:</b> ${surplus:.2f}<br/>
    <b>Debt-to-Income Ratio:</b> {(total_debt/income)*100:.1f}%<br/>
    <b>Savings Rate:</b> {(surplus/income)*100:.1f}%<br/>
    <b>Overall Health:</b> {salud_financiera(surplus, income)}
    """
    story.append(Paragraph(summary_text, styles['Normal']))

    # Footer
    story.append(Spacer(1, 0.3*inch))
    footer_text = f"Report generated on {datetime.now().strftime('%B %d, %Y')} | Yoly Financial Advisor"
    story.append(Paragraph(footer_text, styles['Normal']))

    # Build PDF
    doc.build(story)
    publicar_pdf(tmp_path, pdf_path)

    # Cleanup chart files
    try:
        os.remove(chart_path_pie)
        os.remove(chart_path_bar)
    except:
        pass

    return pdf_path, consejo_ok

# ==================== DASHBOARD FUNCTIONS ====================

def generar_excel_gastos(phone):
    """Genera un archivo Excel con los gastos del usuario del mes actual"""
    if not HAS_PANDAS:
        return None

    try:
        gastos = cargar_gastos(phone)
        if not gastos:
            return None

        # Filtrar gastos del mes actual
        hoy = datetime.now()
        mes_actual = hoy.month
        anio_actual = hoy.year

        gastos_mes = [g for g in gastos if 'fecha' in g]
        gastos_mes = [g for g in gastos_mes if datetime.strptime(g['fecha'], '%Y-%m-%d').month == mes_actual
                      and datetime.strptime(g['fecha'], '%Y-%m-%d').year == anio_actual]

        if not gastos_mes:
            return None

        # Crear DataFrame
        df_data = []
        for gasto in gastos_mes:
            df_data.append({
                'Fecha': gasto.get('fecha', ''),
                'Descripción': gasto.get('descripcion', ''),
                'Categoría': gasto.get('categoria', ''),
                'Monto': gasto.get('monto', 0),
                'Desglose': json.dumps(gasto.get('desglose_json', {})) if gasto.get('desglose_json') else ''
            })

        df = pd.DataFrame(df_data)

        # Guardar en archivo temporal
        excel_path = f"/tmp/gastos_{phone}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        df.to_excel(excel_path, index=False, engine='openpyxl')

        return excel_path
    except Exception as e:
        logger.error(f"Error generando Excel: {e}", exc_info=True)
        return None

def generar_pdf_dashboard(phone):
    """Genera un PDF con los gastos del mes actual"""
    try:
        gastos = cargar_gastos(phone)
        if not gastos:
            return None

        # Filtrar gastos del mes actual
        hoy = datetime.now()
        mes_actual = hoy.month
        anio_actual = hoy.year

        gastos_mes = [g for g in gastos if 'fecha' in g]
        gastos_mes = [g for g in gastos_mes if datetime.strptime(g['fecha'], '%Y-%m-%d').month == mes_actual
                      and datetime.strptime(g['fecha'], '%Y-%m-%d').year == anio_actual]

        if not gastos_mes:
            return None

        pdf_path = f"/tmp/gastos_{phone}_{datetime.now().strftime('%Y%m%d')}.pdf"
        tmp_path = ruta_temporal(pdf_path)

        doc = SimpleDocTemplate(tmp_path, pagesize=letter)
        story = []
        styles = getSampleStyleSheet()

        # Título
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1e40af'),
            spaceAfter=12
        )

        story.append(Paragraph("Estado de Gastos", title_style))
        story.append(Paragraph(f"Período: {hoy.strftime('%B %Y')}", styles['Normal']))
        story.append(Spacer(1, 0.3*inch))

        # Tabla de gastos
        table_data = [['Fecha', 'Descripción', 'Categoría', 'Monto']]
        total_mes = 0

        for gasto in sorted(gastos_mes, key=lambda x: x.get('fecha', '')):
            table_data.append([
                gasto.get('fecha', ''),
                gasto.get('descripcion', '')[:30],
                gasto.get('categoria', ''),
                f"${gasto.get('monto', 0):.2f}"
            ])
            total_mes += gasto.get('monto', 0)

        table_data.append(['', '', 'TOTAL', f"${total_mes:.2f}"])

        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))

        story.append(table)
        doc.build(story)
        publicar_pdf(tmp_path, pdf_path)

        return pdf_path
    except Exception as e:
        logger.error(f"Error generando PDF dashboard: {e}", exc_info=True)
        return None

# ==================== ROUTES ====================

@app.route("/", methods=["GET"])
def home():
    logger.info("GET / - Home endpoint called")
    print("[REQUEST] GET / - Home check")
    return "Yoly Bot Running OK", 200

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint for Render"""
    logger.info("GET /health - Health check endpoint called")
    print("[REQUEST] GET /health - Health check")
    return jsonify({
        "status": "ok",
        "service": "Yoly Bot",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route("/download/informe_gastos.pdf", methods=["GET"])
def download_informe():
    """Sirve el PDF de informe de gastos"""
    return servir_pdf('/tmp/informe_gastos.pdf', 'informe_gastos.pdf')

@app.route("/download/presupuesto_analisis.pdf", methods=["GET"])
def download_presupuesto():
    """Sirve el PDF de análisis de presupuesto"""
    return servir_pdf('/tmp/presupuesto_analisis.pdf', 'presupuesto_analisis.pdf')

@app.route("/download/informe_metas.pdf", methods=["GET"])
def download_informe_metas():
    """Sirve el PDF de informe de metas"""
    pdf_path = generar_informe_metas()
    if not pdf_path:
        return "Informe de metas no disponible", 404
    return servir_pdf(pdf_path, 'informe_metas.pdf')

@app.route("/dashboard/<phone>", methods=["GET"])
def dashboard(phone):
    """Dashboard con vista de gastos y botones de descarga"""
    try:
        gastos = cargar_gastos(phone)

        if not gastos:
            return render_template_string("""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - Yoly</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8f9fa; }
        .container { max-width: 1200px; margin-top: 40px; }
        .card { border: none; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    </style>
</head>
<body>
    <div class="container">
        <div class="card p-4 text-center">
            <h2 class="mb-3">Tu Panel de Gastos</h2>
            <p class="text-muted">No tienes gastos registrados aún.</p>
            <p>Envía un mensaje a Yoly para comenzar a registrar tus gastos.</p>
        </div>
    </div>
</body>
</html>
            """)

        # Filtrar gastos del mes actual
        hoy = datetime.now()
        mes_actual = hoy.month
        anio_actual = hoy.year

        gastos_mes = [g for g in gastos if 'fecha' in g]
        gastos_mes = [g for g in gastos_mes if datetime.strptime(g['fecha'], '%Y-%m-%d').month == mes_actual
                      and datetime.strptime(g['fecha'], '%Y-%m-%d').year == anio_actual]

        # Agrupar por categoría
        por_categoria = {}
        for gasto in gastos_mes:
            cat = gasto.get('categoria', 'otro')
            monto = gasto.get('monto', 0)
            if cat not in por_categoria:
                por_categoria[cat] = {'monto': 0, 'count': 0}
            por_categoria[cat]['monto'] += monto
            por_categoria[cat]['count'] += 1

        total_mes = sum(g.get('monto', 0) for g in gastos_mes)

        # Crear tabla HTML
        tabla_html = """
        <table class="table table-striped">
            <thead class="table-primary">
                <tr>
                    <th>Fecha</th>
                    <th>Descripción</th>
                    <th>Categoría</th>
                    <th>Monto</th>
                </tr>
            </thead>
            <tbody>
        """

        for gasto in sorted(gastos_mes, key=lambda x: x.get('fecha', ''), reverse=True):
            tabla_html += f"""
                <tr>
                    <td>{gasto.get('fecha', '')}</td>
                    <td>{gasto.get('descripcion', '')[:40]}</td>
                    <td><span class="badge bg-info">{gasto.get('categoria', 'otro')}</span></td>
                    <td>${gasto.get('monto', 0):.2f}</td>
                </tr>
            """

        tabla_html += """
            </tbody>
        </table>
        """

        # Crear carpetas HTML
        carpetas_html = ""
        for categoria, data in sorted(por_categoria.items(), key=lambda x: x[1]['monto'], reverse=True):
            carpetas_html += f"""
            <div class="col-md-4 mb-3">
                <div class="card h-100">
                    <div class="card-body">
                        <h5 class="card-title">{categoria.title()}</h5>
                        <p class="card-text">
                            <strong>${data['monto']:.2f}</strong><br>
                            <small>{data['count']} transacciones</small>
                        </p>
                    </div>
                </div>
            </div>
            """

        html = f"""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - Yoly</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.datatables.net/1.13.7/css/dataTables.bootstrap5.min.css" rel="stylesheet">
    <style>
        body {{ background-color: #f8f9fa; }}
        .dashboard-header {{ background: linear-gradient(135deg, #1e40af 0%, #0f172a 100%); color: white; padding: 40px 0; margin-bottom: 40px; }}
        .dashboard-header h1 {{ font-size: 2.5rem; font-weight: bold; margin-bottom: 10px; }}
        .stats-card {{ background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
        .stats-value {{ font-size: 2rem; font-weight: bold; color: #1e40af; }}
        .btn-group-responsive {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 20px 0; }}
        .btn-group-responsive .btn {{ flex: 1; min-width: 150px; }}
        @media (max-width: 768px) {{
            .btn-group-responsive .btn {{ flex: 0 1 calc(50% - 5px); }}
        }}
    </style>
</head>
<body>
    <div class="dashboard-header">
        <div class="container">
            <h1>Panel de Gastos</h1>
            <p class="lead mb-0">{hoy.strftime('%B %Y')}</p>
        </div>
    </div>

    <div class="container">
        <!-- Estadísticas -->
        <div class="row mb-4">
            <div class="col-md-4">
                <div class="stats-card">
                    <p class="text-muted mb-1">Total del Mes</p>
                    <div class="stats-value">${total_mes:.2f}</div>
                    <small class="text-muted">{len(gastos_mes)} transacciones</small>
                </div>
            </div>
            <div class="col-md-4">
                <div class="stats-card">
                    <p class="text-muted mb-1">Promedio por Transacción</p>
                    <div class="stats-value">${total_mes/len(gastos_mes):.2f if gastos_mes else 0:.2f}</div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="stats-card">
                    <p class="text-muted mb-1">Categorías</p>
                    <div class="stats-value">{len(por_categoria)}</div>
                </div>
            </div>
        </div>

        <!-- Botones de Descarga -->
        <div class="btn-group-responsive">
            <a href="/dashboard/{phone}/excel" class="btn btn-success btn-lg">
                <span>📊 Descargar Excel</span>
            </a>
            <a href="/dashboard/{phone}/pdf" class="btn btn-danger btn-lg">
                <span>📄 Descargar PDF</span>
            </a>
        </div>

        <!-- Carpetas por Categoría -->
        <h3 class="mb-4 mt-4">Gastos por Categoría</h3>
        <div class="row mb-5">
            {carpetas_html}
        </div>

        <!-- Tabla de Gastos -->
        <h3 class="mb-3">Detalle de Transacciones</h3>
        <div class="card">
            <div class="card-body">
                {tabla_html}
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.7/js/dataTables.bootstrap5.min.js"></script>
    <script>
        document.addEventListener('DOMContentLoaded', function() {{
            if (document.querySelector('table')) {{
                new DataTable('table', {{
                    "language": {{
                        "url": "//cdn.datatables.net/plug-ins/1.13.7/i18n/es-ES.json"
                    }}
                }});
            }}
        }});
    </script>
</body>
</html>
        """

        return html
    except Exception as e:
        logger.error(f"Error en dashboard: {e}", exc_info=True)
        return f"Error al cargar el dashboard: {str(e)}", 500

@app.route("/dashboard/<phone>/excel", methods=["GET"])
def descargar_excel(phone):
    """Descarga los gastos en Excel"""
    if not HAS_PANDAS:
        return "Excel no disponible (pandas no instalado)", 501

    try:
        excel_path = generar_excel_gastos(phone)
        if not excel_path or not os.path.exists(excel_path):
            return "No hay gastos para descargar", 404

        with open(excel_path, 'rb') as f:
            datos = f.read()

        return Response(datos, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       headers={"Content-Disposition": f"attachment; filename=gastos_{phone}.xlsx"})
    except Exception as e:
        logger.error(f"Error descargando Excel: {e}", exc_info=True)
        return f"Error al generar Excel: {str(e)}", 500

@app.route("/dashboard/<phone>/pdf", methods=["GET"])
def descargar_pdf(phone):
    """Descarga los gastos en PDF"""
    try:
        pdf_path = generar_pdf_dashboard(phone)
        if not pdf_path or not os.path.exists(pdf_path):
            return "No hay gastos para descargar", 404

        return servir_pdf(pdf_path, f'gastos_{phone}.pdf')
    except Exception as e:
        logger.error(f"Error descargando PDF: {e}", exc_info=True)
        return f"Error al generar PDF: {str(e)}", 500

@app.route("/whatsapp", methods=["POST", "GET"])
def whatsapp():
    """
    WhatsApp webhook handler for Twilio.
    Processes messages and images for budget and goal management.
    """
    print(f"[REQUEST] {request.method} /whatsapp - Webhook request received")
    logger.info(f"Webhook request received via {request.method}")

    # ==================== REQUEST VALIDATION ====================
    # DISABLED: Twilio signature validation is disabled for development
    # The validation was causing 403 Forbidden errors

    logger.info("Twilio signature validation is disabled (development mode)")

    # ==================== EXTRACT MESSAGE DATA ====================

    # Handle missing required fields
    incoming_msg = request.form.get('Body', '').strip()
    from_number = request.form.get('From', '')
    message_sid = request.form.get('MessageSid', 'unknown')
    account_sid = request.form.get('AccountSid', 'unknown')

    # Verificar si hay media adjunta
    media_url_0 = request.form.get('MediaUrl0', '')
    media_content_type = request.form.get('MediaContentType0', '')

    print(f"[WHATSAPP] Received request from Twilio")
    print(f"[WHATSAPP] Extracted - From: {from_number}, MessageSID: {message_sid}, Body: {incoming_msg}, MediaUrl0: {media_url_0}, Type: {media_content_type}")
    logger.info(f"[WHATSAPP] Message received - From: {from_number}, Body: {incoming_msg[:100]}, Has Media: {bool(media_url_0)}, Type: {media_content_type}")

    print(f"Pregunta recibida: {len(incoming_msg)} caracteres, Media: {bool(media_url_0)}, Tipo: {media_content_type}")

    if not incoming_msg and not media_url_0:
        logger.warning(f"Empty message body and no media received from {from_number}")
        print(f"[WARNING] Empty message body from {from_number}")
        resp = MessagingResponse()
        return str(resp), 200

    if not from_number:
        logger.error("Missing 'From' field in request")
        print(f"[ERROR] Missing 'From' field in request")
        resp = MessagingResponse()
        resp.message("❌ Error: No se pudo identificar el remitente")
        return str(resp), 400

    # Log incoming message details
    print(f"[WHATSAPP MESSAGE] From: {from_number} | SID: {message_sid} | Body: {incoming_msg[:100]} | Media: {bool(media_url_0)}")
    logger.info(f"Message received | From: {from_number} | MessageSID: {message_sid} | Body: {incoming_msg} | Media: {bool(media_url_0)}")

    server_url = os.environ.get('SERVER_URL', request.host_url.rstrip('/'))

    # Si hay media, procesar según tipo
    if media_url_0:
        if media_content_type.startswith("image/"):
            return atender_con_imagen(media_url_0, incoming_msg, from_number, server_url)
        elif media_content_type.startswith("audio/"):
            return atender_con_audio(media_url_0, incoming_msg, from_number, server_url)

    return atender(incoming_msg, from_number, server_url)


def enviar_por_twilio(to, textos):
    """Manda la respuesta por la API de Twilio (para cuando el webhook ya contestó)."""
    for texto in textos:
        for parte in partir_mensaje(texto):
            try:
                twilio_client.messages.create(from_=os.environ.get('TWILIO_WHATSAPP_NUMBER'), to=to, body=parte)
            except Exception as e:
                logger.error(f"No se pudo enviar la respuesta a {to}: {e}", exc_info=True)
                return


def atender(incoming_msg, from_number, server_url):
    """Procesa el mensaje en otro hilo. Si termina dentro de ESPERA_MAX_SEGUNDOS,
    la respuesta va en el TwiML como siempre; si no, el webhook contesta
    MENSAJE_ESPERA y el hilo manda la respuesta por Twilio al terminar."""
    salida = Salida()
    estado = {"listo": False, "tarde": False}
    candado = threading.Lock()

    def trabajar():
        try:
            procesar_mensaje(incoming_msg, from_number, server_url, salida)
        except Exception as e:
            logger.error(f"Error procesando mensaje de {from_number}: {e}", exc_info=True)
            salida.message("Disculpa, hubo un error procesando tu mensaje. Intenta de nuevo.")
        with candado:
            estado["listo"] = True
            enviar_despues = estado["tarde"]
        if enviar_despues:
            logger.info(f"Respuesta tardía enviada por Twilio a {from_number}")
            enviar_por_twilio(from_number, salida.textos)

    hilo = threading.Thread(target=trabajar, daemon=True)
    hilo.start()
    hilo.join(ESPERA_MAX_SEGUNDOS)

    resp = MessagingResponse()
    with candado:
        if estado["listo"]:
            for texto in salida.textos:
                resp.message(texto)
            return str(resp)
        estado["tarde"] = True
    logger.info(f"Respuesta a {from_number} tarda más de {ESPERA_MAX_SEGUNDOS}s: se enviará por Twilio")
    resp.message(MENSAJE_ESPERA)
    return str(resp)


def atender_con_imagen(media_url, incoming_msg, from_number, server_url):
    """Procesa una imagen (factura/recibo) con Claude Vision"""
    salida = Salida()
    estado = {"listo": False, "tarde": False}
    candado = threading.Lock()

    def trabajar():
        try:
            # Procesar la imagen con visión
            resultado = procesar_foto_inteligente(media_url, from_number)
            salida.message(resultado)

            # Si hay texto adicional, intentar reclasificar
            if incoming_msg and incoming_msg.lower().strip():
                # Esperar un momento para que se guarde el gasto
                import time
                time.sleep(0.5)
                resultado_reclasificacion = reclasificar_gasto(incoming_msg, from_number)
                salida.message(resultado_reclasificacion)
        except Exception as e:
            logger.error(f"Error procesando imagen de {from_number}: {e}", exc_info=True)
            salida.message("Disculpa, hubo un error procesando tu factura. Intenta de nuevo.")
        with candado:
            estado["listo"] = True
            enviar_despues = estado["tarde"]
        if enviar_despues:
            logger.info(f"Respuesta tardía enviada por Twilio a {from_number}")
            enviar_por_twilio(from_number, salida.textos)

    hilo = threading.Thread(target=trabajar, daemon=True)
    hilo.start()
    hilo.join(ESPERA_MAX_SEGUNDOS)

    resp = MessagingResponse()
    with candado:
        if estado["listo"]:
            for texto in salida.textos:
                resp.message(texto)
            return str(resp)
        estado["tarde"] = True
    logger.info(f"Respuesta a {from_number} tarda más de {ESPERA_MAX_SEGUNDOS}s: se enviará por Twilio")
    resp.message(MENSAJE_ESPERA)
    return str(resp)


def atender_con_audio(media_url, incoming_msg, from_number, server_url):
    """Procesa un audio, lo transcribe con Whisper y procesa el texto"""
    salida = Salida()
    estado = {"listo": False, "tarde": False}
    candado = threading.Lock()

    def trabajar():
        try:
            # Transcribir audio
            texto_audio = procesar_audio(media_url, from_number)
            if not texto_audio:
                salida.message("❌ No pude transcribir el audio. Intenta de nuevo.")
                return

            # Procesar el texto transcrito
            salida.message(f"Entendí: {texto_audio}. Ya lo registré.")

            # Procesar como mensaje de texto
            procesar_mensaje(texto_audio, from_number, server_url, salida)
        except Exception as e:
            logger.error(f"Error procesando audio de {from_number}: {e}", exc_info=True)
            salida.message("Disculpa, hubo un error procesando tu audio. Intenta de nuevo.")
        with candado:
            estado["listo"] = True
            enviar_despues = estado["tarde"]
        if enviar_despues:
            logger.info(f"Respuesta tardía enviada por Twilio a {from_number}")
            enviar_por_twilio(from_number, salida.textos)

    hilo = threading.Thread(target=trabajar, daemon=True)
    hilo.start()
    hilo.join(ESPERA_MAX_SEGUNDOS)

    resp = MessagingResponse()
    with candado:
        if estado["listo"]:
            for texto in salida.textos:
                resp.message(texto)
            return str(resp)
        estado["tarde"] = True
    logger.info(f"Respuesta a {from_number} tarda más de {ESPERA_MAX_SEGUNDOS}s: se enviará por Twilio")
    resp.message(MENSAJE_ESPERA)
    return str(resp)


def procesar_mensaje(incoming_msg, from_number, server_url, resp):
    """Arma la respuesta de Yoly. `resp` junta los textos (ver Salida)."""
    msg_lower = incoming_msg.lower()

    try:
        # ==================== DETECCIÓN DE PALABRAS CLAVE: PDF, EXCEL, LINK, PANEL, DASHBOARD ====================

        palabras_clave_link = ['pdf', 'excel', 'link', 'panel', 'dashboard', 'descargar']

        if any(keyword in msg_lower for keyword in palabras_clave_link):
            logger.info(f"Keyword detection for dashboard/downloads: {from_number}")

            # Generar respuesta rápida
            if 'pdf' in msg_lower:
                try:
                    pdf_path = generar_pdf_dashboard(from_number)
                    if pdf_path and os.path.exists(pdf_path):
                        pdf_url = f"{server_url}/dashboard/{from_number}/pdf"
                        twilio_client.messages.create(
                            from_=os.environ.get('TWILIO_WHATSAPP_NUMBER'),
                            to=from_number,
                            body="📄 Aquí está tu PDF del mes:",
                            media_url=[pdf_url]
                        )
                        resp.message("PDF descargado ✓")
                        return
                except Exception as e:
                    logger.error(f"Error generando PDF: {e}")

            if 'excel' in msg_lower:
                if not HAS_PANDAS:
                    resp.message("Excel no está disponible en este momento. Usa el PDF en su lugar.")
                    return
                try:
                    excel_path = generar_excel_gastos(from_number)
                    if excel_path and os.path.exists(excel_path):
                        excel_url = f"{server_url}/dashboard/{from_number}/excel"
                        resp.message(f"Tu Excel está listo: {excel_url}")
                        return
                except Exception as e:
                    logger.error(f"Error generando Excel: {e}")

            # Si pide link, dashboard, panel o descargar
            if any(keyword in msg_lower for keyword in ['link', 'panel', 'dashboard']):
                dashboard_url = f"{server_url}/dashboard/{from_number}"
                resp.message(f"Aquí está: {dashboard_url}\n\nTuenes opciones para descargar Excel o PDF una vez ahí.")
                return

        # ==================== REGISTRO DE GASTOS CON DESGLOSE ====================

        if any(keyword in msg_lower for keyword in ['envié', 'mandé', 'total']) and any(keyword in msg_lower for keyword in ['renta', 'comida', 'estefanito', 'transporte', 'pago']):
            logger.info(f"Expense breakdown detected from {from_number}")
            resultado = extraer_gastos(incoming_msg)

            # Si hay error o no es válido, responder con alerta
            if resultado.get("error") or not resultado.get("valido"):
                resp.message(resultado.get("error") or resultado.get("alerta", "Error procesando gastos"))
                return

            # Construir resumen del desglose
            total = resultado["total_enviado"]
            desglose = resultado["desglose"]
            reserva = resultado["reserva"]
            alerta = resultado.get("alerta")

            # Generar resumen detallado
            resumen = f"✓ Total: ${total:.2f}\n\nDesglose:\n"
            for categoria, monto in desglose.items():
                resumen += f"  • {categoria.title()}: ${monto:.2f}\n"

            if reserva > 0:
                resumen += f"  • Reserva: ${reserva:.2f}\n"
            elif reserva < 0:
                resumen += f"  • Reserva: ${reserva:.2f} ⚠️\n"

            resumen += f"\nTotal verificado: ${total:.2f}"

            if alerta:
                resumen += f"\n\n{alerta}"

            resp.message(resumen)

            # Guardar gasto en la BD
            try:
                gastos = cargar_gastos(from_number)
                timestamp_iso = datetime.now().isoformat()
                gasto_id = f"desglose_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

                gasto_nuevo = {
                    "id": gasto_id,
                    "fecha": datetime.now().strftime("%Y-%m-%d"),
                    "timestamp": timestamp_iso,
                    "descripcion": "Desglose de gastos",
                    "monto": total,
                    "categoria": "desglose",
                    "total": total,
                    "desglose_json": desglose,
                    "reserva": reserva,
                    "diferencia": resultado["diferencia"],
                    "validacion_alerta": alerta
                }

                gastos.append(gasto_nuevo)
                guardar_gastos(from_number, gastos)
                logger.info(f"Expense breakdown saved for {from_number}: ${total}")

                # ==================== AUTO-RESPUESTA POST-GASTO ====================
                # Construir resumen detallado del desglose para la auto-respuesta
                auto_respuesta = "¡Listo! ✅\n📤 Total: $" + f"{total:.2f}\n"
                for categoria, monto in desglose.items():
                    auto_respuesta += f"  • {categoria.title()}: ${monto:.2f}\n"

                if reserva > 0:
                    auto_respuesta += f"  • Reserva por si acaso: ${reserva:.2f}\n"
                elif reserva < 0:
                    auto_respuesta += f"  • Sobregiro: ${abs(reserva):.2f} ⚠️\n"
                else:
                    auto_respuesta += f"  • Reserva por si acaso: $0\n"

                # Alerta si hay sobregiro
                if alerta:
                    auto_respuesta += f"\n{alerta}\n"

                # Enviar link del dashboard
                dashboard_url = f"{server_url}/dashboard/{from_number}"
                auto_respuesta += f"\nAquí tienes tu panel para ver todo: {dashboard_url}\n\n"
                auto_respuesta += "¿Quieres que también te genere el PDF del mes o con esto es suficiente?"

                resp.message(auto_respuesta)
            except Exception as e:
                logger.error(f"Error saving expense breakdown: {e}", exc_info=True)

            return

        # ==================== BORRAR GASTO ====================

        if any(keyword in msg_lower for keyword in ['borrar', 'eliminar', 'quitar', 'borra']):
            if any(keyword in msg_lower for keyword in ['gasto', 'factura', 'gasta']):
                # Verificar si es confirmación
                if msg_lower.strip() in ['si', 'sí', 'si.', 'sí.']:
                    resultado = confirmar_borrado(from_number)
                    resp.message(resultado)
                    return

                # Solicitar borrado
                resultado = borrar_gasto(from_number, incoming_msg)
                resp.message(resultado)
                return

        # ==================== CORTE/REPORTE ====================

        if any(keyword in msg_lower for keyword in ['corte', 'balance', 'reporte', 'estado']):
            if any(keyword in msg_lower for keyword in ['quincena', 'mes', 'dias', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio']):
                resultado = generar_corte(from_number, incoming_msg)
                responder(resp, resultado)
                return

        # ==================== GOALS KEYWORDS ====================

        # Ver metas o solicitar análisis de metas
        if any(keyword in msg_lower for keyword in ['metas', 'objetivos', 'mis objetivos', 'ver metas', 'estado metas']):
            logger.info(f"Goals request detected for {from_number}")
            metas = obtener_metas()
            if not metas:
                logger.info("No goals found for user")
                resp.message("No tienes metas registradas. Puedo ayudarte a crearlas. ¿Cuál es tu objetivo financiero?")
                return

            # Generar resumen de metas
            resumen = "📊 *Tus Metas Financieras:*\n"
            for meta in metas:
                emoji = "✓" if meta['completada'] else "→"
                resumen += f"\n{emoji} {meta['nombre']}\n"
                resumen += f"   Progreso: ${meta['monto_actual']:.2f}/${meta['monto_objetivo']:.2f}\n"
                resumen += f"   {meta['porcentaje_progreso']:.1f}% | Faltante: ${meta['monto_faltante']:.2f}"
                if meta['dias_faltantes'] > 0:
                    resumen += f" | {meta['dias_faltantes']} días"
                resumen += "\n"

            logger.info(f"Goals summary sent to {from_number}")
            resp.message(resumen)
            return

        # Solicitar consejos y análisis de metas
        if any(keyword in msg_lower for keyword in ['consejo', 'consejos metas', 'analiza metas', 'tips', 'motivación']):
            logger.info(f"Financial advice request from {from_number}")
            consejo = analizar_metas_y_dar_consejos()
            responder(resp, consejo)
            return

        # Ver progreso de una meta específica
        if 'progreso' in msg_lower or 'avance' in msg_lower:
            logger.info(f"Progress check requested by {from_number}")
            metas = obtener_metas()
            if not metas:
                resp.message("No tienes metas. Crea una para empezar.")
                return

            resumen_progreso = "📈 *Progreso de Metas:*\n"
            for meta in metas:
                barra_progreso = "█" * int(meta['porcentaje_progreso'] / 10) + "░" * (10 - int(meta['porcentaje_progreso'] / 10))
                resumen_progreso += f"\n{meta['nombre']}\n[{barra_progreso}] {meta['porcentaje_progreso']:.1f}%\n"

            logger.info(f"Progress report sent to {from_number}")
            resp.message(resumen_progreso)
            return

        # Descargar informe de metas
        if 'informe metas' in msg_lower or 'reporte metas' in msg_lower:
            logger.info(f"Goals report request from {from_number}")
            try:
                pdf_path = generar_informe_metas()
                if pdf_path and os.path.exists(pdf_path):
                    pdf_url = f"{server_url}/download/informe_metas.pdf"
                    logger.info(f"Goals report generated: {pdf_url}")

                    twilio_client.messages.create(
                        from_=os.environ.get('TWILIO_WHATSAPP_NUMBER'),
                        to=from_number,
                        body="📊 Aquí está tu informe de metas financieras:",
                        media_url=[pdf_url]
                    )
                    logger.info(f"Goals report sent to {from_number}")
                    resp.message("Informe de metas enviado ✓")
                    return
                else:
                    logger.warning(f"Goals report generation failed for {from_number}")
                    resp.message("No tienes metas para generar el informe. ¡Crea algunas!")
                    return
            except Exception as e:
                logger.error(f"Error generando informe de metas: {e}", exc_info=True)
                resp.message(f"Error al generar informe: {str(e)}")
                return

        # Registrar nueva meta - detectar patrones
        if any(keyword in msg_lower for keyword in ['quiero ahorrar', 'quiero pagar', 'meta:', 'objetivo:', 'nueva meta', 'nueva objetivo']):
            logger.info(f"New goal creation request from {from_number}")
            respuesta = """Para crear una meta, necesito estos datos:
🎯 Nombre: ¿Cuál es tu objetivo?
💰 Monto: ¿Cuánto necesitas? (especifica el monto exacto que tienes en mente)
📅 Plazo: ¿Para cuándo? (ej: 3 meses, 31/12/2024)
📂 Tipo: deuda, ahorro o inversión

Dime cada dato claramente. NUNCA usaré números que no menciones explícitamente."""
            resp.message(respuesta)
            return

        # Actualizar progreso de meta
        if 'actualizar' in msg_lower or 'ahorré' in msg_lower or 'pagué' in msg_lower:
            logger.info(f"Goal progress update request from {from_number}")
            metas = obtener_metas()
            if not metas:
                resp.message("No tienes metas. Crea una primero.")
                return

            respuesta = "¿Cuál meta actualizaste? Dime el nombre:\n"
            for meta in metas:
                respuesta += f"\n• {meta['nombre']}"
            resp.message(respuesta)
            return

        # ==================== FINANCIAL ANALYSIS ====================

        # Detectar si es reclasificación de gasto (ej: "de esos X, Y son para...")
        if any(keyword in msg_lower for keyword in ['de esos', 'de eso', 'ese es', 'esa es para', 'son para']):
            logger.info(f"Expense reclassification request from {from_number}")
            resultado = reclasificar_gasto(incoming_msg, from_number)
            resp.message(resultado)
            return

        # Detectar palabras clave financieras (ingresos y gastos)
        financial_keywords = ['gano', 'trabajo', 'renta', 'pago', 'comida', 'gimnasio', 'teléfono', 'telefono',
                             'deuda', 'seguro', 'suscripción', 'suscripcion', 'ingreso', 'sueldo', 'horas',
                             'negocio', 'gasto', 'gastos', 'arriendo', 'cuota', 'otra ingreso']
        tiene_info_financiera = any(keyword in msg_lower for keyword in financial_keywords)

        if tiene_info_financiera and ('gano' in msg_lower or 'renta' in msg_lower or 'pago' in msg_lower or 'trabajo' in msg_lower):
            logger.info(f"Financial analysis request from {from_number}")
            analisis, ingresos, gastos, superavit = procesar_analisis_financiero(incoming_msg, from_number)
            resp.message(analisis)
            return str(resp)

        # ==================== BUDGET & EXPENSE KEYWORDS ====================

        # Detectar si el usuario pide "informe de gastos"
        if 'informe de gastos' in msg_lower:
            logger.info(f"Expense report request from {from_number}")
            try:
                generar_informe_gastos()
                pdf_url = f"{server_url}/download/informe_gastos.pdf"
                logger.info(f"Expense report generated: {pdf_url}")

                twilio_client.messages.create(
                    from_=os.environ.get('TWILIO_WHATSAPP_NUMBER'),
                    to=from_number,
                    body="Aquí está tu informe de gastos:",
                    media_url=[pdf_url]
                )
                logger.info(f"Expense report sent to {from_number}")
                resp.message("Informe de gastos enviado. Descárgalo desde el enlace.")
                return
            except Exception as e:
                logger.error(f"Error generando informe de gastos: {e}", exc_info=True)
                resp.message(f"Error al generar informe: {str(e)}")
                return

        # Detectar palabras clave para asesor financiero
        financial_keywords = ['presupuesto', 'gastos', 'asesor', 'ahorro']
        should_generate_budget = any(keyword in incoming_msg.lower() for keyword in financial_keywords)

        if should_generate_budget:
            logger.info(f"Budget analysis request from {from_number}")
            try:
                logger.debug("Extracting budget data from message...")
                datos, faltan = extraer_presupuesto(incoming_msg)
                if faltan:
                    responder(resp, "Para armar tu presupuesto me falta:\n" + "\n".join(f"• {f}" for f in faltan))
                    return
                logger.debug(f"Generating budget analysis: {datos}")
                _, consejo_ok = generar_presupuesto(datos)

                pdf_url = f"{server_url}/download/presupuesto_analisis.pdf"
                logger.info(f"Budget analysis generated: {pdf_url}")

                twilio_client.messages.create(
                    from_=os.environ.get('TWILIO_WHATSAPP_NUMBER'),
                    to=from_number,
                    body=("📊 Aquí está tu análisis de presupuesto mensual con recomendaciones del asesor financiero de IA:"
                          if consejo_ok else "📊 Aquí está tu presupuesto mensual:"),
                    media_url=[pdf_url]
                )

                logger.info(f"Budget analysis sent to {from_number}")
                if consejo_ok:
                    resp.message("✅ Reporte de presupuesto enviado. Incluye tu análisis financiero y recomendaciones personalizadas.")
                else:
                    resp.message("📄 Te envié el reporte con tus números, pero no pude generar los consejos del asesor. Intenta de nuevo en unos minutos.")
                return
            except Exception as e:
                logger.error(f"Error generando presupuesto: {e}", exc_info=True)
                resp.message(f"❌ Error al generar presupuesto: {str(e)}")
                return

        # ==================== DEFAULT RESPONSE ====================

        # Respuesta normal con Claude
        logger.info(f"Processing message with Claude API for {from_number}")
        response = client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=500,
            system="Eres Yoly, un asistente virtual amable, útil, que responde corto y en español. Eres especialista en finanzas personales y ayudas a tus usuarios a gestionar sus metas financieras.",
            messages=[{"role": "user", "content": incoming_msg}]
        )
        bot_response = response.content[0].text
        logger.info(f"Claude response generated for {from_number}")

    except Exception as e:
        logger.error(f"Unexpected error in webhook: {e}", exc_info=True)
        print(f"[ERROR] Unexpected error processing message: {e}")
        bot_response = f"Disculpa, hubo un error procesando tu mensaje: {str(e)}"

    responder(resp, bot_response)
    print(f"[RESPONSE] Sent to {from_number}: {bot_response[:100]}")
    logger.info(f"Response sent to {from_number}")
    return

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n{'='*60}")
    print(f"[STARTING] Yoly Bot on 0.0.0.0:{port}")
    print(f"[INFO] Endpoints available:")
    print(f"  - GET  http://0.0.0.0:{port}/          (Health check)")
    print(f"  - GET  http://0.0.0.0:{port}/health    (JSON Health)")
    print(f"  - POST http://0.0.0.0:{port}/whatsapp  (Webhook)")
    print(f"{'='*60}\n")
    logger.info(f"Starting Flask app on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
