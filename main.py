import os
os.environ["MPLBACKEND"] = "Agg"  # sin ventanas: los gráficos se dibujan fuera del hilo principal (Mac)
import json
import logging
import threading
import io
import re
from datetime import datetime, timedelta
from calendar import monthrange
from flask import Flask, request, jsonify, Response, render_template_string, send_file
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
import html
from urllib.parse import quote_plus
import precios
import reportes
import agent_reporter
import app as orquestador  # 4 agentes: Portero, Ojo, Calculadora, Contadora
import io
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    from openpyxl import Workbook
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

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
PREGUNTA_TABLA = agent_reporter.PREGUNTA_TABLA

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

# ==================== TEMPORARY EXPENSES FOR CONFIRMATION FLOW ====================
# Global dict to store expenses temporarily until user confirms with SI/NO
temp_gastos = {}

# ==================== TEMPORARY PRODUCTS FOR PRICE LIBRARY ====================
# Global dict to store products extracted from tickets/receipts temporarily
temp_productos = {}

# ==================== USER MEMORY PERSISTENCE ====================
# Global dict to store user memory by phone number
memoria_usuarios = {}

def cargar_memoria():
    """Carga memoria global existente desde archivo si existe"""
    memoria_archivo = '/app/data/memoria_global.json'
    if os.path.exists(memoria_archivo):
        try:
            with open(memoria_archivo, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def guardar_memoria(memoria):
    """Guarda memoria global en archivo"""
    memoria_archivo = '/app/data/memoria_global.json'
    try:
        os.makedirs(os.path.dirname(memoria_archivo), exist_ok=True)
        with open(memoria_archivo, 'w', encoding='utf-8') as f:
            json.dump(memoria, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Error guardando memoria: {e}")

# Carga memoria al inicio
memoria_usuarios = cargar_memoria()

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

DATA_DIR = '/home/claude/yoly/data'

def obtener_ruta_datos(telefono):
    """Obtiene la ruta de la carpeta de datos del usuario"""
    ruta = f'{DATA_DIR}/{telefono}'
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

# ==================== COBRO DE DEUDA (lista de pagos) ====================


# Cuentas en Python puro: viven en la Calculadora (agent_calculator)
from agent_calculator import (a_numero, fecha_valida, METODOS_PAGO, normalizar_metodo,
                              normalizar_pagos, fecha_corta, armar_cobro)

def guardar_cobro(phone_clean, datos):
    """Guarda el cobro en memoria_global.json y en data/{telefono}/cobro_deuda.json"""
    memoria_usuarios[phone_clean] = datos
    guardar_memoria(memoria_usuarios)
    try:
        ruta = f'{DATA_DIR}/{phone_clean}'
        os.makedirs(ruta, exist_ok=True)
        with open(f'{ruta}/cobro_deuda.json', 'w', encoding='utf-8') as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Error guardando cobro_deuda.json: {e}")

def es_registro_pagos(gasto):
    """Registros viejos donde la foto de pagos se guardó como 1 solo gasto"""
    return isinstance(gasto.get('pagos'), list) and (len(gasto['pagos']) > 1 or gasto.get('cliente'))

def obtener_cobro(phone):
    """Busca el cobro de deuda del usuario por sus últimos 10 dígitos (memoria, archivo o registro viejo)"""
    digitos = normalizar_telefono(phone)
    ultimos10 = digitos[-10:]
    if not ultimos10:
        return None

    candidatos = [digitos] + [k for k in memoria_usuarios if normalizar_telefono(k)[-10:] == ultimos10]
    for clave in candidatos:
        datos = memoria_usuarios.get(clave) or {}
        if datos.get('tipo') in ('cobro_deuda', 'deuda') and datos.get('pagos'):
            return armar_cobro(datos)

    if os.path.isdir(DATA_DIR):
        for carpeta in os.listdir(DATA_DIR):
            if normalizar_telefono(carpeta)[-10:] != ultimos10:
                continue
            archivo = f'{DATA_DIR}/{carpeta}/cobro_deuda.json'
            if os.path.exists(archivo):
                try:
                    with open(archivo, 'r', encoding='utf-8') as f:
                        return armar_cobro(json.load(f))
                except Exception as e:
                    logger.warning(f"Error leyendo {archivo}: {e}")
            for gasto in reversed(cargar_gastos(carpeta)):
                if es_registro_pagos(gasto):
                    return armar_cobro({"cliente": gasto.get('cliente'), "deuda": gasto.get('monto'),
                                        "pagos": gasto.get('pagos'), "fecha": gasto.get('fecha', '')})
    return None

def cargar_gastos_usuario(phone):
    """Gastos del usuario buscando la carpeta por últimos 10 dígitos (whatsapp:+593... o 593...)"""
    ultimos10 = normalizar_telefono(phone)[-10:]
    gastos = []
    if ultimos10 and os.path.isdir(DATA_DIR):
        for carpeta in sorted(os.listdir(DATA_DIR)):
            if normalizar_telefono(carpeta)[-10:] == ultimos10:
                gastos.extend(cargar_gastos(carpeta))
    return [g for g in gastos if not es_registro_pagos(g)]

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

def huellas_vistas(telefono):
    """Huellas de las fotos que ya se procesaron (para que el Portero detecte repetidas)."""
    archivo = f"{obtener_ruta_datos(telefono)}/imagenes_vistas.json"
    if os.path.exists(archivo):
        try:
            with open(archivo, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []

def marcar_vistas(telefono, huellas):
    vistas = huellas_vistas(telefono)
    vistas.extend(h for h in huellas if h not in vistas)
    try:
        with open(f"{obtener_ruta_datos(telefono)}/imagenes_vistas.json", 'w', encoding='utf-8') as f:
            json.dump(vistas[-500:], f)
    except Exception as e:
        logger.warning(f"No pude guardar las huellas de fotos: {e}")

def guardar_imagen_factura(telefono, webp_bytes):
    """Guarda la foto en data/{telefono}/facturas/ y devuelve la ruta"""
    ruta_facturas = f"{obtener_ruta_datos(telefono)}/facturas"
    os.makedirs(ruta_facturas, exist_ok=True)
    ahora = datetime.now()
    ruta_archivo = f"{ruta_facturas}/{ahora.strftime('%Y-%m-%d')}_{ahora.strftime('%Y%m%d_%H%M%S_%f')}.webp"
    with open(ruta_archivo, "wb") as f:
        f.write(webp_bytes)
    return ruta_archivo

def guardar_gasto_factura(telefono, vision_response, pagos, pagado, ruta_archivo):
    """Factura/recibo normal: un solo gasto. Si es factura de compra, también alimenta el comparador."""
    cliente = vision_response.get('cliente') or 'Cliente'
    descripcion = vision_response.get('descripcion') or 'Gasto'
    es_compra = vision_response.get('tipo') == 'factura_compra'

    fecha_gasto = pagos[0].get('fecha') or vision_response.get('fecha') or datetime.now().strftime("%Y-%m-%d")
    if not fecha_valida(fecha_gasto):
        fecha_gasto = datetime.now().strftime("%Y-%m-%d")
    timestamp_formato = datetime.now().strftime("%Y%m%d_%H%M")
    desc_normalizada = descripcion.lower().replace(' ', '').replace('-', '')[:15]
    gasto_nuevo = {
        "id": f"{timestamp_formato}_{desc_normalizada}_{int(pagado)}",
        "fecha": fecha_gasto,
        "timestamp": datetime.now().isoformat(),
        "descripcion": descripcion,
        "categoria": reportes.CARPETA_COMPRAS if es_compra else "otro",
        "monto": pagado,
        "cliente": cliente,
        "factura_path": ruta_archivo
    }
    gastos = cargar_gastos(telefono)
    gastos.append(gasto_nuevo)
    guardar_gastos(telefono, gastos)

    respuesta = f"Leí {cliente}: ${pagado:,.0f}."
    if es_compra:
        try:
            reportes.guardar_en_carpeta(DATA_DIR, telefono, reportes.CARPETA_COMPRAS,
                                        dict(gasto_nuevo, movimiento="gasto", tienda=vision_response.get('tienda')))
        except Exception as e:
            logger.warning(f"No pude guardar la compra en su carpeta: {e}")
        # Factura de compra: sus productos alimentan el comparador de precios de la ciudad
        try:
            texto_precios = registrar_precios_factura(
                telefono, vision_response.get('tienda') or cliente, vision_response.get('ciudad'),
                vision_response.get('articulos'), fecha_gasto)
            if texto_precios:
                respuesta += "\n\n" + texto_precios
        except Exception as e:
            logger.error(f"Error guardando precios de la factura: {e}", exc_info=True)
    return respuesta

def contexto_agentes(telefono):
    """Lo que el orquestador (app.py) necesita de main para guardar cada cosa en su lugar."""
    phone_clean = normalizar_telefono(telefono)

    def marcar_pregunta_tabla():
        memoria_usuarios.setdefault(phone_clean, {})['ultima_pregunta'] = 'dashboard'
        guardar_memoria(memoria_usuarios)

    return orquestador.Contexto(
        cliente=client,
        modelo=MODELO_CLAUDE,
        phone_clean=phone_clean,
        guardar_imagen=lambda webp: guardar_imagen_factura(telefono, webp),
        guardar_cobro=guardar_cobro,
        cobro_actual=lambda: obtener_cobro(phone_clean),
        guardar_gasto=lambda vision, pagos, total, ruta: guardar_gasto_factura(telefono, vision, pagos, total, ruta),
        guardar_transferencia=lambda vision, pagos, total, ruta, texto: guardar_transferencia(
            telefono, vision, pagos, total, ruta, texto),
        huellas_vistas=lambda: huellas_vistas(telefono),
        marcar_vistas=lambda huellas: marcar_vistas(telefono, huellas),
        marcar_pregunta_tabla=marcar_pregunta_tabla,
    )

def procesar_fotos_whatsapp(media_urls, telefono, texto_usuario="", info=None):
    """
    Descarga las fotos de Twilio, las pasa a WEBP y las manda por los 4 agentes (app.procesar_fotos):
    Portero (tipo y repetidas) -> Ojo (Vision) -> Calculadora (sumas en Python) -> Contadora (respuesta).
    """
    try:
        imagenes = []
        for media_url in media_urls:
            imagen_bytes = descargar_media_twilio(media_url)
            if not imagen_bytes:
                return "❌ No pude descargar la imagen. Intenta de nuevo."
            webp_bytes = convertir_a_webp(imagen_bytes)
            if not webp_bytes:
                return "❌ No pude procesar la imagen. Intenta con otra."
            imagenes.append(webp_bytes)
        return orquestador.procesar_fotos(imagenes, texto_usuario, contexto_agentes(telefono), info)
    except Exception as e:
        logger.error(f"Error en procesar_fotos_whatsapp: {e}", exc_info=True)
        return f"❌ Error procesando factura: {str(e)}"

def procesar_foto_inteligente(media_url, telefono, texto_usuario="", info=None):
    """
    Procesa una foto de factura/recibo/libretita/transferencia (ver procesar_fotos_whatsapp).
    texto_usuario: lo que el usuario escribió junto a la foto (ayuda a clasificar transferencias).
    info: dict opcional donde se anota el tipo de documento leído (info["tipo"]).
    """
    return procesar_fotos_whatsapp([media_url], telefono, texto_usuario, info)

NOMBRES_CARPETA = {"sueldo": "Sueldos", "renta": "Renta", "deuda": "Deudas", "servicios": "Servicios",
                   "ingreso": "Ingresos", "compras": "Compras", "por_revisar": "Por revisar"}


def guardar_transferencia(telefono, vision_response, pagos, monto, ruta_archivo, texto_usuario=""):
    """
    Clasifica una transferencia por su concepto (sueldo, renta, deuda, servicios, ingreso) y la guarda en
    data/{telefono}/{carpeta}/transacciones.json, y también en gastos.json o ingresos.json para los reportes.
    Si el concepto no dice qué es, va a "por_revisar" y se le pregunta al usuario (no adivinamos).
    """
    concepto = vision_response.get('concepto') or ''
    direccion = vision_response.get('direccion')
    clase = reportes.clasificar_transferencia(concepto, direccion, texto_usuario)
    carpeta, movimiento = clase['carpeta'], clase['movimiento']

    fecha = pagos[0].get('fecha') if pagos else None
    fecha_leida = fecha_valida(fecha) if fecha else False
    if not fecha_leida:
        fecha = datetime.now().strftime("%Y-%m-%d")
    persona = vision_response.get('beneficiario') if movimiento == 'gasto' else vision_response.get('ordenante')
    persona = persona or vision_response.get('cliente') or ''
    descripcion = concepto or vision_response.get('descripcion') or 'Transferencia'
    if persona and persona.lower() not in descripcion.lower():
        descripcion = f"{descripcion} - {persona}"
    revisar = carpeta == reportes.CARPETA_REVISAR or not fecha_leida

    transaccion = {
        "id": f"transf_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
        "fecha": fecha,
        "timestamp": datetime.now().isoformat(),
        "descripcion": descripcion,
        "concepto": concepto,
        "persona": persona,
        "categoria": carpeta,
        "movimiento": movimiento,
        "monto": monto,
        "banco": vision_response.get('banco') or '',
        "referencia": vision_response.get('referencia') or '',
        "factura_path": ruta_archivo,
        "revisar": revisar,
    }
    reportes.guardar_en_carpeta(DATA_DIR, telefono, carpeta, transaccion)

    # La misma transacción en la lista que usan los reportes (gastos.json o ingresos.json)
    if movimiento == 'ingreso':
        archivo = f"{obtener_ruta_datos(telefono)}/ingresos.json"
        ingresos = []
        if os.path.exists(archivo):
            try:
                with open(archivo, 'r', encoding='utf-8') as f:
                    ingresos = json.load(f)
            except Exception:
                ingresos = []
        ingresos.append(transaccion)
        with open(archivo, 'w', encoding='utf-8') as f:
            json.dump(ingresos, f, ensure_ascii=False, indent=2)
    else:
        gastos = cargar_gastos(telefono)
        gastos.append(transaccion)
        guardar_gastos(telefono, gastos)

    flecha = "recibiste de" if movimiento == 'ingreso' else "enviaste a"
    lineas = [f"🏦 Transferencia: ${monto:,.2f}" + (f" ({flecha} {persona})" if persona else "")]
    if concepto:
        lineas.append(f"Concepto: {concepto}")
    lineas.append(f"Fecha: {datetime.strptime(fecha, '%Y-%m-%d').strftime('%d/%m/%Y')}"
                  + ("" if fecha_leida else " (no vi la fecha, usé hoy; revísala)"))
    if carpeta == reportes.CARPETA_REVISAR:
        lineas.append("")
        lineas.append("❓ No sé si es sueldo, renta, deuda o servicios, así que la guardé en *Por revisar*.")
        lineas.append("Respóndeme por ejemplo \"es renta\" o \"es sueldo\" y la muevo.")
    else:
        lineas.append(f"{reportes.EMOJI_CARPETA.get(carpeta, '📁')} La guardé en la carpeta *{NOMBRES_CARPETA[carpeta]}*.")
        if not direccion:
            lineas.append("(No se ve si la enviaste o la recibiste; la tomé como dinero que enviaste.)")
        lineas.append("Si no es eso, dime por ejemplo \"es deuda\" y la cambio.")
    return "\n".join(lineas)


def mover_transferencia(telefono, carpeta, server_url):
    """Respuesta a "es renta" / "ponlo en sueldo": mueve la última transferencia a esa carpeta."""
    resultado = reportes.mover_transaccion(DATA_DIR, telefono, carpeta)
    if not resultado:
        return None
    t, anterior = resultado
    phone_clean = normalizar_telefono(telefono)
    if anterior == carpeta:
        return f"Esa transferencia (${t.get('monto', 0):,.2f}) ya estaba en *{NOMBRES_CARPETA[carpeta]}* 👍"
    return (f"Listo ✅ Moví la transferencia de ${t.get('monto', 0):,.2f} ({t.get('descripcion', '')}) "
            f"de *{NOMBRES_CARPETA.get(anterior, anterior)}* a *{NOMBRES_CARPETA[carpeta]}*.\n\n"
            f"📁 Tus carpetas: {server_url}/dashboard/{phone_clean}/carpetas")


# Palabras que piden un resumen; además el mensaje tiene que traer un período ("del 1 al 20 de junio")
PALABRAS_RESUMEN = re.compile(r'\b(resumen|informe|reporte|corte|balance|excel|pdf|cuanto (?:gaste|gane|entro|salio|he gastado))\b')
# "gastos de junio" también, pero solo en mensajes cortos sin montos ("gastos de hoy: comida $20" es registrar)
PALABRAS_RESUMEN_CORTO = re.compile(r'\b(gaste|gastado|gastos|ingresos|movimientos)\b')


def pedido_resumen_periodo(texto):
    """Periodo pedido en el mensaje o None. "informe de gastos" sin fechas = este mes."""
    t = reportes.sin_acentos(texto).lower()
    if 'meta' in t or 'presupuesto' in t:
        return None
    corto = len(t.split()) <= 8 and not re.search(r'\$\s*\d|\d+\s*(?:dolares|usd)', t)
    if not (PALABRAS_RESUMEN.search(t) or (corto and PALABRAS_RESUMEN_CORTO.search(t))):
        return None
    periodo = reportes.parsear_periodo(t)
    if not periodo and re.search(r'\b(informe|resumen|reporte) de (mis )?gastos\b', t):
        periodo = reportes.parsear_periodo('este mes')
    return periodo


def resumen_periodo_usuario(telefono, inicio, fin):
    gastos, ingresos = reportes.cargar_movimientos(DATA_DIR, telefono)
    movs, sin_fecha = reportes.movimientos_periodo(gastos, ingresos, inicio, fin)
    return reportes.resumir(movs), sin_fecha


def responder_resumen_periodo(telefono, periodo, server_url):
    """Texto de WhatsApp con totales, barras por categoría y links a PDF, Excel y panel del período."""
    phone_clean = normalizar_telefono(telefono)
    resumen, sin_fecha = resumen_periodo_usuario(telefono, periodo['inicio'], periodo['fin'])
    texto = reportes.texto_resumen(resumen, periodo, sin_fecha)
    if resumen['movimientos']:
        q = f"desde={periodo['inicio'].isoformat()}&hasta={periodo['fin'].isoformat()}"
        texto += (f"\n\n📄 PDF con gráfico: {server_url}/download/periodo/{phone_clean}/pdf?{q}"
                  f"\n📊 Excel: {server_url}/download/periodo/{phone_clean}/excel?{q}"
                  f"\n🌐 Panel: {server_url}/dashboard/{phone_clean}/periodo?{q}")
    return texto


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

def normalizar_telefono(phone):
    """Normaliza el número de teléfono removiendo caracteres no numéricos"""
    if not phone:
        return ""
    # Solo números
    return re.sub(r'[^0-9]', '', phone)

def gastos_del_mes(phone):
    """Gastos del mes actual del usuario (sin los registros de pagos de deuda)"""
    hoy = datetime.now()
    gastos_mes = []
    for g in cargar_gastos_usuario(phone):
        if not fecha_valida(g.get('fecha', '')):
            continue
        f = datetime.strptime(g['fecha'], '%Y-%m-%d')
        if f.month == hoy.month and f.year == hoy.year:
            gastos_mes.append(g)
    return sorted(gastos_mes, key=lambda x: x.get('fecha', ''))

# Estado de cuenta (PDF/Excel de la libretita): lo arma la Contadora (agent_reporter)
titulo_cobro = agent_reporter.titulo_cobro
estilo_tabla = agent_reporter.estilo_tabla

def generar_excel_gastos(phone):
    """Excel con openpyxl. Cobro de deuda: hojas Pagos y Resumen. Si no: gastos del mes."""
    if not HAS_OPENPYXL:
        return None
    try:
        phone_clean = normalizar_telefono(phone)
        excel_path = f"/tmp/gastos_{phone_clean}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        cobro = obtener_cobro(phone_clean)
        if cobro:
            with open(excel_path, 'wb') as f:
                f.write(agent_reporter.excel_cobro(cobro))
            return excel_path

        from openpyxl.styles import Font, PatternFill
        gastos_mes = gastos_del_mes(phone_clean)
        if not gastos_mes:
            return None
        wb = Workbook()
        ws = wb.active
        ws.title = "Transacciones"
        ws.append(["Fecha", "Descripción", "Categoría", "Monto"])
        for celda in ws[ws.max_row]:
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = PatternFill("solid", fgColor="1E40AF")
        for g in gastos_mes:
            ws.append([g.get('fecha', ''), g.get('descripcion', ''), g.get('categoria', 'otro'), g.get('monto', 0)])
        ws.append(["", "", "TOTAL", sum(g.get('monto', 0) for g in gastos_mes)])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = max(12, max(len(str(c.value or '')) for c in col) + 2)
        for fila in ws.iter_rows(min_row=2):
            for celda in fila:
                if isinstance(celda.value, (int, float)) and celda.column_letter != 'A':
                    celda.number_format = '"$"#,##0.00'
        wb.save(excel_path)
        return excel_path
    except Exception as e:
        logger.error(f"Error generando Excel: {e}", exc_info=True)
        return None

def generar_pdf_dashboard(phone):
    """PDF con tabla. Cobro de deuda: estado de cuenta con pagos. Si no: gastos del mes."""
    try:
        phone_clean = normalizar_telefono(phone)
        cobro = obtener_cobro(phone_clean)
        gastos_mes = [] if cobro else gastos_del_mes(phone_clean)
        if not cobro and not gastos_mes:
            return None

        hoy = datetime.now()
        pdf_path = f"/tmp/gastos_{phone_clean}_{hoy.strftime('%Y%m%d')}.pdf"
        tmp_path = ruta_temporal(pdf_path)
        if cobro:
            with open(tmp_path, 'wb') as f:
                f.write(agent_reporter.pdf_cobro(cobro))
            publicar_pdf(tmp_path, pdf_path)
            return pdf_path

        doc = SimpleDocTemplate(tmp_path, pagesize=letter)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=15,
                                     textColor=colors.HexColor('#1e40af'), spaceAfter=6)
        story = [Paragraph("Reporte de Gastos del Mes", title_style),
                 Paragraph(f"Fecha: {hoy.strftime('%d/%m/%Y')}", styles['Normal']),
                 Spacer(1, 0.3*inch)]
        data = [['Fecha', 'Descripción', 'Categoría', 'Monto']]
        for g in gastos_mes:
            data.append([g.get('fecha', ''), g.get('descripcion', '')[:30], g.get('categoria', 'otro'), f"${g.get('monto', 0):,.2f}"])
        data.append(['', '', 'TOTAL', f"${sum(g.get('monto', 0) for g in gastos_mes):,.2f}"])
        tabla = Table(data)
        tabla.setStyle(estilo_tabla('#1e40af', '#f3f4f6'))
        story.append(tabla)

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

DASHBOARD_COBRO_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#3B82F6">
    <link rel="manifest" href="/manifest.json">
    <title>Yoly - {{ titulo }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
</head>
<body class="bg-gradient-to-br from-blue-50 via-white to-indigo-50 min-h-screen">
    <div class="bg-gradient-to-r from-blue-600 to-indigo-600 text-white py-6">
        <div class="container mx-auto px-4 max-w-5xl">
            <h1 class="text-2xl md:text-3xl font-bold mb-1">💳 Estado de Cuenta {{ cobro.cliente }}</h1>
            <p class="text-blue-100">{{ hoy_str }} | •••{{ phone_display }}</p>
        </div>
    </div>

    <div class="container mx-auto px-4 py-8 max-w-5xl">
        <!-- Deuda | Pagado | Te falta -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div class="bg-white rounded-lg shadow-md p-6 border-l-4 border-blue-500">
                <p class="text-gray-600 text-sm font-medium mb-2">Deuda</p>
                <div class="text-3xl font-bold text-blue-600">${{ "{:,.0f}".format(cobro.deuda) }}</div>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6 border-l-4 border-green-500">
                <p class="text-gray-600 text-sm font-medium mb-2">Pagado</p>
                <div class="text-3xl font-bold text-green-600">${{ "{:,.0f}".format(cobro.pagado) }}</div>
                <p class="text-xs text-gray-500 mt-2">{{ cobro.pagos|length }} pagos</p>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6 border-l-4 {{ 'border-red-500' if cobro.saldo > 0 else 'border-green-500' }}">
                <p class="text-gray-600 text-sm font-medium mb-2">Te falta</p>
                <div class="text-3xl font-bold {{ 'text-red-600' if cobro.saldo > 0 else 'text-green-600' }}">${{ "{:,.0f}".format(cobro.saldo) }}</div>
            </div>
        </div>

        {% if cobro.frecuencia_dias %}
        <div class="bg-blue-50 border-l-4 border-blue-500 p-4 mb-6 rounded text-blue-800">
            📅 Paga cada <strong>{{ cobro.frecuencia_dias }} días</strong> promedio
        </div>
        {% endif %}

        <div class="flex flex-wrap gap-3 mb-8">
            <a href="/download/excel/{{ phone_clean }}" class="flex-1 md:flex-none bg-green-500 hover:bg-green-600 text-white font-bold py-3 px-6 rounded-lg text-center">📊 Descargar Excel</a>
            <a href="/download/pdf/{{ phone_clean }}" class="flex-1 md:flex-none bg-red-500 hover:bg-red-600 text-white font-bold py-3 px-6 rounded-lg text-center">📄 Descargar PDF</a>
            <a href="/dashboard/{{ phone_clean }}/precios" class="flex-1 md:flex-none bg-amber-500 hover:bg-amber-600 text-white font-bold py-3 px-6 rounded-lg text-center">🛒 Comparar precios</a>
            <a href="/dashboard/{{ phone_clean }}/periodo" class="flex-1 md:flex-none bg-indigo-500 hover:bg-indigo-600 text-white font-bold py-3 px-6 rounded-lg text-center">📅 Resumen por fechas</a>
            <a href="/dashboard/{{ phone_clean }}/carpetas" class="flex-1 md:flex-none bg-slate-600 hover:bg-slate-700 text-white font-bold py-3 px-6 rounded-lg text-center">📁 Carpetas</a>
        </div>

        <div class="bg-white rounded-lg shadow-lg p-6 mb-8">
            <h2 class="text-xl font-bold text-gray-800 mb-4">Historial de Pagos</h2>
            <div style="position: relative; height: 280px;"><canvas id="grafico"></canvas></div>
        </div>

        <div class="bg-white rounded-lg shadow-lg overflow-hidden">
            <div class="bg-gray-50 px-6 py-4 border-b">
                <h2 class="text-xl font-bold text-gray-800">Detalle de Pagos</h2>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full border-collapse text-sm">
                    <thead>
                        <tr class="bg-gray-100 border-b-2 border-gray-300">
                            <th class="text-left px-4 py-3 font-semibold text-gray-700">Fecha</th>
                            <th class="text-right px-4 py-3 font-semibold text-gray-700">Monto</th>
                            <th class="text-left px-4 py-3 font-semibold text-gray-700">Metodo</th>
                            <th class="text-right px-4 py-3 font-semibold text-gray-700">Saldo Restante</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr class="bg-blue-50 border-b">
                            <td class="px-4 py-3 text-gray-700">Deuda inicial</td>
                            <td class="px-4 py-3"></td>
                            <td class="px-4 py-3"></td>
                            <td class="px-4 py-3 text-right font-semibold">${{ "{:,.2f}".format(cobro.deuda) }}</td>
                        </tr>
                        {% for f in cobro.filas %}
                        <tr class="{{ 'bg-white' if loop.index is odd else 'bg-gray-50' }} border-b">
                            <td class="px-4 py-3 text-gray-700">{{ f.fecha_corta }}</td>
                            <td class="px-4 py-3 text-right font-semibold text-green-700">${{ "{:,.2f}".format(f.monto) }}</td>
                            <td class="px-4 py-3 {{ 'text-gray-400 italic' if f.metodo == 'no especificado' else 'text-gray-700' }}">{{ f.metodo|capitalize }}{% if f.nota %} <span class="text-xs text-gray-500">({{ f.nota }})</span>{% endif %}</td>
                            <td class="px-4 py-3 text-right font-semibold {{ 'text-red-600' if f.saldo > 0 else 'text-green-600' }}">${{ "{:,.2f}".format(f.saldo) }}</td>
                        </tr>
                        {% endfor %}
                        <tr class="bg-gray-200 font-bold">
                            <td class="px-4 py-3">TOTAL PAGADO</td>
                            <td class="px-4 py-3 text-right">${{ "{:,.2f}".format(cobro.pagado) }}</td>
                            <td class="px-4 py-3"></td>
                            <td class="px-4 py-3 text-right {{ 'text-red-600' if cobro.saldo > 0 else 'text-green-600' }}">${{ "{:,.2f}".format(cobro.saldo) }}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        if (window.Chart) {
            new Chart(document.getElementById('grafico'), {
                data: {
                    labels: {{ grafico_labels|tojson }},
                    datasets: [
                        {type: 'bar', label: 'Pago', data: {{ grafico_pagos|tojson }}, backgroundColor: '#10b981', yAxisID: 'y'},
                        {type: 'line', label: 'Saldo restante', data: {{ grafico_saldos|tojson }}, borderColor: '#dc2626', backgroundColor: '#dc2626', tension: 0.2, yAxisID: 'y1'}
                    ]
                },
                options: {
                    maintainAspectRatio: false,
                    scales: {
                        y: {beginAtZero: true, position: 'left', title: {display: true, text: 'Pago $'}},
                        y1: {beginAtZero: true, position: 'right', grid: {drawOnChartArea: false}, title: {display: true, text: 'Saldo $'}}
                    }
                }
            });
        }
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js').catch(() => {});
        }
    </script>
</body>
</html>
"""

@app.route("/dashboard/<phone>", methods=["GET"])
def dashboard(phone):
    """Dashboard con vista de gastos y botones de descarga - Tailwind CSS + PWA"""
    try:
        # Limpia phone: solo números
        phone_clean = re.sub(r'[^0-9]', '', phone)
        phone_last10 = phone_clean[-10:] if len(phone_clean) >= 10 else phone_clean
        phone_display = phone_last10[-4:] if phone_last10 else "?????"

        logger.info(f"Dashboard request: phone={phone}, phone_clean={phone_clean}, phone_last10={phone_last10}, phone_display={phone_display}")

        # Registro de pagos de una deuda: dashboard de estado de cuenta
        cobro = obtener_cobro(phone_clean)
        if cobro:
            return render_template_string(DASHBOARD_COBRO_HTML,
                cobro=cobro,
                titulo=titulo_cobro(cobro),
                phone_clean=phone_clean,
                phone_display=phone_display,
                hoy_str=datetime.now().strftime('%d/%m/%Y'),
                grafico_labels=[f['fecha_corta'] if f['fecha'] else f"Pago {i}" for i, f in enumerate(cobro['filas'], 1)],
                grafico_pagos=[f['monto'] for f in cobro['filas']],
                grafico_saldos=[f['saldo'] for f in cobro['filas']])

        # Busca gastos por últimos 10 dígitos (carpetas whatsapp:+593... o 593...)
        gastos = cargar_gastos_usuario(phone_clean)

        if not gastos:
            return render_template_string("""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#3B82F6">
    <meta name="description" content="Panel de finanzas Yoly - Control de envios USA a Ecuador">
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" type="image/png" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E%3Crect fill='%233B82F6' width='192' height='192'/%3E%3Ctext x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-size='120' fill='white' font-family='Arial,sans-serif' font-weight='bold'%3EY%3C/text%3E%3C/svg%3E">
    <title>Yoly - Panel de Finanzas</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .fadeIn { animation: fadeIn 0.6s ease-out; }
    </style>
</head>
<body class="bg-gradient-to-br from-blue-50 to-indigo-50 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-2xl fadeIn">
        <div class="bg-white rounded-2xl shadow-xl p-8 text-center">
            <div class="text-5xl mb-4">📭</div>
            <h1 class="text-3xl font-bold text-gray-800 mb-2">Aún no hay gastos</h1>
            <p class="text-gray-600 text-lg mb-6">¡Hola {{ phone_display }}! Comienza a registrar tus gastos para ver tu panel aquí.</p>

            <div class="bg-blue-50 border-l-4 border-blue-500 p-4 mb-6 rounded">
                <p class="text-blue-800">
                    <strong>Tip:</strong> Envía un audio a Yoly con tus gastos del día:
                    <br/><em>"Envié 500, 380 de renta y 120 de comida"</em>
                </p>
            </div>

            <a href="https://wa.me/593" class="inline-block bg-green-500 hover:bg-green-600 text-white font-bold py-3 px-8 rounded-lg transition transform hover:scale-105">
                💬 Chatear con Yoly vía WhatsApp
            </a>

            <p class="text-gray-500 text-sm mt-8">Yoly te ayuda a controlar envios de USA a Ecuador sin Excel</p>
        </div>
    </div>

    <script>
        console.log('Phone:', '{{ phone_clean }}');
        console.log('Empty dashboard loaded');
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js').catch(err => console.log('SW registration failed'));
        }
        if (localStorage) {
            localStorage.setItem('yoly_phone', '{{ phone_clean }}');
        }
    </script>
</body>
</html>
            """, phone_display=phone_display, phone_clean=phone_clean)

        # Filtrar gastos del mes actual
        hoy = datetime.now()
        mes_actual = hoy.month
        anio_actual = hoy.year

        gastos_mes = gastos_del_mes(phone_clean)

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

        # Crear tabla HTML con Tailwind
        tabla_html = """
        <table class="w-full border-collapse">
            <thead>
                <tr class="bg-gray-100 border-b-2 border-gray-300">
                    <th class="text-left px-4 py-3 font-semibold text-gray-700">Fecha</th>
                    <th class="text-left px-4 py-3 font-semibold text-gray-700">Descripción</th>
                    <th class="text-left px-4 py-3 font-semibold text-gray-700">Categoría</th>
                    <th class="text-right px-4 py-3 font-semibold text-gray-700">Monto</th>
                </tr>
            </thead>
            <tbody>
        """

        for idx, gasto in enumerate(sorted(gastos_mes, key=lambda x: x.get('fecha', ''), reverse=True)):
            bg_class = "bg-white" if idx % 2 == 0 else "bg-gray-50"
            tabla_html += f"""
                <tr class="{bg_class} border-b hover:bg-blue-50 transition">
                    <td class="px-4 py-3 text-gray-700">{gasto.get('fecha', '')}</td>
                    <td class="px-4 py-3 text-gray-700">{gasto.get('descripcion', '')[:40]}</td>
                    <td class="px-4 py-3"><span class="inline-block bg-blue-100 text-blue-800 px-3 py-1 rounded-full text-sm font-medium">{gasto.get('categoria', 'otro')}</span></td>
                    <td class="px-4 py-3 text-right font-semibold text-gray-900">${gasto.get('monto', 0):.2f}</td>
                </tr>
            """

        tabla_html += """
            </tbody>
        </table>
        """

        # Crear carpetas HTML con Tailwind
        carpetas_html = ""
        categoria_icons = {
            'renta': '🏠', 'comida': '🍽️', 'transporte': '🚗', 'servicios': '💡',
            'internet': '📡', 'telefono': '📱', 'utilidades': '💰', 'estefanito': '👶',
            'materiales': '🔨', 'envio_ecuador': '📦', 'otro': '📋'
        }

        for categoria, data in sorted(por_categoria.items(), key=lambda x: x[1]['monto'], reverse=True):
            icon = categoria_icons.get(categoria, '📋')
            carpetas_html += f"""
            <div class="bg-white rounded-lg shadow hover:shadow-lg transition transform hover:scale-105 p-5 cursor-pointer">
                <div class="text-3xl mb-2">{icon}</div>
                <h3 class="font-bold text-gray-800 text-lg">{categoria.title()}</h3>
                <p class="text-2xl font-bold text-blue-600 mt-2">${data['monto']:.2f}</p>
                <p class="text-sm text-gray-500 mt-1">{data['count']} transacción{"es" if data['count'] != 1 else ""}</p>
            </div>
            """

        # Format values for template
        hoy_str = hoy.strftime('%B %Y')
        gastos_count = len(gastos_mes)
        promedio = total_mes/len(gastos_mes) if gastos_mes else 0
        categorias_count = len(por_categoria)

        html_template = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#3B82F6">
    <meta name="description" content="Panel de finanzas Yoly - Control de envios USA a Ecuador">
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" type="image/png" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E%3Crect fill='%233B82F6' width='192' height='192'/%3E%3Ctext x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-size='120' fill='white' font-family='Arial,sans-serif' font-weight='bold'%3EY%3C/text%3E%3C/svg%3E">
    <title>Yoly - Panel de Finanzas</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @keyframes slideDown { from { opacity: 0; transform: translateY(-20px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .slideDown { animation: slideDown 0.4s ease-out; }
        .fadeIn { animation: fadeIn 0.6s ease-out forwards; }
        .card-item { animation-delay: calc(var(--index) * 100ms); }
        table { font-size: 0.875rem; }
    </style>
</head>
<body class="bg-gradient-to-br from-blue-50 via-white to-indigo-50 min-h-screen">
    <!-- Header -->
    <div class="bg-gradient-to-r from-blue-600 to-indigo-600 text-white py-6 slideDown">
        <div class="container mx-auto px-4 max-w-6xl">
            <h1 class="text-3xl font-bold mb-1">💰 Yoly - Panel de Finanzas</h1>
            <p class="text-blue-100">{{ hoy_str }} | {{ phone_display }}</p>
        </div>
    </div>

    <div class="container mx-auto px-4 py-8 max-w-6xl">
        <!-- Stats Cards -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8 fadeIn">
            <div class="bg-white rounded-lg shadow-md p-6 border-l-4 border-blue-500">
                <p class="text-gray-600 text-sm font-medium mb-2">Total del Mes</p>
                <div class="text-3xl font-bold text-blue-600">${{ total_mes|round(2) }}</div>
                <p class="text-xs text-gray-500 mt-2">{{ gastos_count }} transacciones</p>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6 border-l-4 border-green-500">
                <p class="text-gray-600 text-sm font-medium mb-2">Promedio por Transacción</p>
                <div class="text-3xl font-bold text-green-600">${{ promedio|round(2) }}</div>
            </div>
            <div class="bg-white rounded-lg shadow-md p-6 border-l-4 border-purple-500">
                <p class="text-gray-600 text-sm font-medium mb-2">Categorías</p>
                <div class="text-3xl font-bold text-purple-600">{{ categorias_count }}</div>
            </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex flex-wrap gap-3 mb-8 fadeIn" style="animation-delay: 200ms;">
            <a href="/download/excel/{{ phone_clean }}" class="flex-1 md:flex-none bg-green-500 hover:bg-green-600 text-white font-bold py-3 px-6 rounded-lg transition transform hover:scale-105 inline-block text-center">
                📊 Descargar Excel
            </a>
            <a href="/download/pdf/{{ phone_clean }}" class="flex-1 md:flex-none bg-red-500 hover:bg-red-600 text-white font-bold py-3 px-6 rounded-lg transition transform hover:scale-105 inline-block text-center">
                📄 Descargar PDF
            </a>
            <a href="/dashboard/{{ phone_clean }}/precios" class="flex-1 md:flex-none bg-amber-500 hover:bg-amber-600 text-white font-bold py-3 px-6 rounded-lg transition transform hover:scale-105 inline-block text-center">
                🛒 Comparar precios
            </a>
            <a href="/dashboard/{{ phone_clean }}/periodo" class="flex-1 md:flex-none bg-indigo-500 hover:bg-indigo-600 text-white font-bold py-3 px-6 rounded-lg transition transform hover:scale-105 inline-block text-center">
                📅 Resumen por fechas
            </a>
            <a href="/dashboard/{{ phone_clean }}/carpetas" class="flex-1 md:flex-none bg-slate-600 hover:bg-slate-700 text-white font-bold py-3 px-6 rounded-lg transition transform hover:scale-105 inline-block text-center">
                📁 Carpetas
            </a>
            <button onclick="compartir()" class="flex-1 md:flex-none bg-blue-500 hover:bg-blue-600 text-white font-bold py-3 px-6 rounded-lg transition transform hover:scale-105">
                📤 Compartir
            </button>
        </div>

        <!-- Categories Grid -->
        <div class="mb-8">
            <h2 class="text-2xl font-bold text-gray-800 mb-4">Gastos por Categoría</h2>
            <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 fadeIn" style="animation-delay: 300ms;">
                {{ carpetas_html|safe }}
            </div>
        </div>

        <!-- Transactions Table -->
        <div class="bg-white rounded-lg shadow-lg overflow-hidden fadeIn" style="animation-delay: 400ms;">
            <div class="bg-gray-50 px-6 py-4 border-b">
                <h2 class="text-xl font-bold text-gray-800">Detalle de Transacciones</h2>
            </div>
            <div class="overflow-x-auto">
                {{ tabla_html|safe }}
            </div>
        </div>

        <!-- Footer -->
        <div class="text-center mt-12 text-gray-600 text-sm">
            <p>Yoly te ayuda a controlar envios de USA a Ecuador sin Excel ✨</p>
            <p class="mt-1">Creado por Jaime | <a href="https://wa.me/593" class="text-blue-600 hover:underline">Contactar</a></p>
        </div>
    </div>

    <script>
        // Debug logging
        console.log('Phone:', '{{ phone_clean }}');
        console.log('Gastos:', {{ gastos_count }});
        console.log('Total:', {{ total_mes|round(2) }});

        // PWA Registration
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js').catch(err => console.log('SW registration failed'));
        }

        // localStorage
        if (localStorage) {
            localStorage.setItem('yoly_phone', '{{ phone_clean }}');
            localStorage.setItem('yoly_dashboard_visited', new Date().toISOString());
        }

        // Share functionality
        function compartir() {
            const text = 'Mi panel de finanzas en Yoly: •••{{ phone_display }}';
            if (navigator.share) {
                navigator.share({
                    title: 'Yoly - Panel de Finanzas',
                    text: text
                }).catch(err => alert('Error al compartir'));
            } else {
                alert('Link del panel: ' + window.location.href);
            }
        }

        // Detect PWA
        if (window.matchMedia('(display-mode: standalone)').matches) {
            console.log('Yoly PWA activa');
        }
    </script>
</body>
</html>
        """

        return render_template_string(html_template,
            phone_clean=phone_clean,
            phone_display=phone_display,
            hoy_str=hoy_str,
            total_mes=total_mes,
            gastos_count=gastos_count,
            promedio=promedio,
            categorias_count=categorias_count,
            carpetas_html=carpetas_html,
            tabla_html=tabla_html)
    except Exception as e:
        logger.error(f"Error en dashboard: {e}", exc_info=True)
        return f"Error al cargar el dashboard: {str(e)}", 500

@app.route("/download/excel/<phone>", methods=["GET"])
@app.route("/dashboard/<phone>/excel", methods=["GET"])
def descargar_excel(phone):
    """Descarga Excel (openpyxl): hojas Pagos y Resumen si es cobro de deuda"""
    if not HAS_OPENPYXL:
        return "Excel no disponible (falta openpyxl)", 501
    try:
        phone_clean = normalizar_telefono(phone)
        excel_path = generar_excel_gastos(phone_clean)
        if not excel_path or not os.path.exists(excel_path):
            return "No hay datos para descargar todavía", 404
        with open(excel_path, 'rb') as f:
            datos = f.read()
        nombre = f"estado_cuenta_{phone_clean[-4:]}.xlsx" if obtener_cobro(phone_clean) else f"gastos_{phone_clean[-4:]}.xlsx"
        return Response(datos, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={nombre}"})
    except Exception as e:
        logger.error(f"Error descargando Excel: {e}", exc_info=True)
        return f"Error al generar Excel: {str(e)}", 500

@app.route("/download/pdf/<phone>", methods=["GET"])
@app.route("/dashboard/<phone>/pdf", methods=["GET"])
def descargar_pdf(phone):
    """Descarga PDF con tabla de pagos (o gastos del mes)"""
    try:
        phone_clean = normalizar_telefono(phone)
        pdf_path = generar_pdf_dashboard(phone_clean)
        if not pdf_path or not os.path.exists(pdf_path):
            return "No hay datos para descargar todavía", 404
        nombre = f"estado_cuenta_{phone_clean[-4:]}.pdf" if obtener_cobro(phone_clean) else f"gastos_{phone_clean[-4:]}.pdf"
        return servir_pdf(pdf_path, nombre)
    except Exception as e:
        logger.error(f"Error descargando PDF: {e}", exc_info=True)
        return f"Error al generar PDF: {str(e)}", 500

DASHBOARD_PRECIOS_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; margin: 0; padding: 16px; color: #1f2937; }
.container { max-width: 1000px; margin: 0 auto; background: white; padding: 24px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
h1 { color: #1e40af; margin-top: 0; }
h2 { margin-top: 32px; color: #111827; }
.sub { color: #6b7280; margin-top: -8px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 20px 0; }
.stat-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 16px; border-radius: 8px; text-align: center; }
.stat-card h3 { margin: 0 0 8px 0; font-size: 13px; opacity: 0.9; }
.stat-card .value { font-size: 26px; font-weight: bold; }
.tabla { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { background: #1e40af; color: white; padding: 10px; text-align: left; font-weight: 600; white-space: nowrap; }
td { padding: 10px; border-bottom: 1px solid #eee; vertical-align: top; }
.price { font-weight: bold; color: #16a34a; white-space: nowrap; }
.caro { color: #dc2626; font-weight: bold; white-space: nowrap; }
.chico { font-size: 12px; color: #6b7280; }
.btn { display: inline-block; background: #f59e0b; color: white; text-decoration: none; padding: 6px 10px; border-radius: 6px; font-size: 12px; font-weight: bold; white-space: nowrap; }
.btn:hover { background: #d97706; }
.barra { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.barra .nombre { width: 140px; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.barra .fondo { flex: 1; background: #eef2ff; border-radius: 4px; }
.barra .relleno { background: #6366f1; color: white; font-size: 12px; padding: 4px 6px; border-radius: 4px; white-space: nowrap; }
.barra .relleno.min { background: #16a34a; }
.vacio { text-align: center; color: #6b7280; padding: 24px; background: #f9fafb; border-radius: 8px; }
form { margin: 8px 0 0; }
select { padding: 6px; border-radius: 6px; border: 1px solid #d1d5db; max-width: 100%; }
a.volver { color: #1e40af; }
"""


def _e(texto):
    return html.escape(str(texto if texto is not None else ''))


@app.route("/dashboard/<phone>/precios", methods=["GET"])
def dashboard_precios(phone):
    """Biblioteca de precios del usuario + comparativas de su ciudad (datos de todos los usuarios)."""
    try:
        phone_clean = normalizar_telefono(phone)
        ruta = f"{DATA_DIR}/{phone_clean}/biblioteca_precios.json"
        mis_precios = []
        if os.path.exists(ruta):
            try:
                with open(ruta, 'r', encoding='utf-8') as f:
                    mis_precios = json.load(f)
            except Exception:
                mis_precios = []

        ciudad = (precios.normalizar_ciudad(request.args.get('ciudad'))[1]
                  or precios.ciudad_usuario(DATA_DIR, phone_clean))
        comparativas = precios.comparativas_ciudad(DATA_DIR, ciudad) if ciudad else []
        ranking = precios.ranking_tiendas(DATA_DIR, ciudad) if ciudad else []
        muestras_ciudad = precios.cargar_ciudad(DATA_DIR, precios.normalizar_ciudad(ciudad)[0]) if ciudad else []

        partes = [f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Comparador de Precios</title><style>{DASHBOARD_PRECIOS_CSS}</style></head>
<body><div class="container">
<p><a class="volver" href="/dashboard/{_e(phone_clean)}">← Volver a mi panel</a></p>
<h1>🛒 Comparador de Precios</h1>"""]

        if not ciudad:
            partes.append("""<div class="vacio"><p>📍 Aún no sé en qué ciudad compras.</p>
<p>Escríbele a Yoly por WhatsApp: <strong>mi ciudad es Quito</strong> (con tu ciudad) y aquí verás
dónde es más barato cada producto en tu ciudad.</p></div>""")
        else:
            partes.append(f"""<p class="sub">Precios de facturas reales de usuarios en <strong>{_e(ciudad)}</strong>
(últimos {precios.DIAS_VIGENCIA} días)</p>
<div class="stats">
<div class="stat-card"><h3>Precios en {_e(ciudad)}</h3><div class="value">{len(muestras_ciudad)}</div></div>
<div class="stat-card"><h3>Tiendas</h3><div class="value">{len({m.get('tienda') for m in muestras_ciudad})}</div></div>
<div class="stat-card"><h3>Productos comparables</h3><div class="value">{len(comparativas)}</div></div>
</div>
<h2>📊 Comparativas en {_e(ciudad)}</h2>""")
            if not comparativas:
                partes.append("""<div class="vacio">Todavía no hay un mismo producto en 2 tiendas distintas de tu ciudad.
Cada factura que mandes a Yoly suma precios. 📸</div>""")
            else:
                partes.append("""<div class="tabla"><table><tr><th>Producto</th><th>Más barato</th><th>Más caro</th>
<th>Diferencia</th><th>Tiendas</th><th></th></tr>""")
                for comp in comparativas[:60]:
                    barato, caro = comp['tiendas'][0], comp['tiendas'][-1]
                    mapa = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{barato['tienda']} {ciudad}")
                    grafico = (f"/dashboard/{quote_plus(phone_clean)}/precios?producto={quote_plus(comp['clave'])}"
                               f"&ciudad={quote_plus(ciudad)}#grafico")
                    partes.append(f"""<tr><td><a href="{_e(grafico)}"><strong>{_e(comp['producto'])}</strong></a>
<div class="chico">{_e(comp['medida'] or 'sin medida')}</div></td>
<td><span class="price">${barato['precio']:.2f}</span><div class="chico">{_e(barato['tienda'])}</div></td>
<td><span class="caro">${caro['precio']:.2f}</span><div class="chico">{_e(caro['tienda'])}</div></td>
<td class="price">${comp['ahorro']:.2f}</td><td>{len(comp['tiendas'])}</td>
<td><a class="btn" href="{_e(mapa)}" target="_blank" rel="noopener">💰 Ahorrar aquí</a></td></tr>""")
                partes.append("</table></div>")

                # Gráfico: precio promedio del artículo en cada tienda de la ciudad
                elegido = next((c for c in comparativas if c['clave'] == request.args.get('producto')), comparativas[0])
                opciones = "".join(
                    f'<option value="{_e(c["clave"])}"{" selected" if c is elegido else ""}>'
                    f'{_e(c["producto"])} {_e(c["medida"])}</option>' for c in comparativas[:60])
                maximo = max(t['precio_prom'] for t in elegido['tiendas']) or 1
                minimo = min(t['precio_prom'] for t in elegido['tiendas'])
                partes.append(f"""<h2 id="grafico">📈 Precio promedio de {_e(elegido['producto'])} {_e(elegido['medida'])} en tu ciudad</h2>
<form method="get">{'<input type="hidden" name="ciudad" value="' + _e(request.args.get('ciudad')) + '">' if request.args.get('ciudad') else ''}<select name="producto" onchange="this.form.submit()">{opciones}</select></form>""")
                for t in sorted(elegido['tiendas'], key=lambda t: t['precio_prom']):
                    ancho = max(18, round(t['precio_prom'] / maximo * 100))
                    clase = "relleno min" if t['precio_prom'] == minimo else "relleno"
                    partes.append(f"""<div class="barra"><div class="nombre">{_e(t['tienda'])}</div>
<div class="fondo"><div class="{clase}" style="width:{ancho}%">${t['precio_prom']:.2f}
<span style="opacity:.8">({t['muestras']})</span></div></div></div>""")

            if ranking:
                partes.append(f"<h2>🏆 Tiendas más baratas en {_e(ciudad)}</h2><div class=\"tabla\"><table>"
                              "<tr><th>#</th><th>Tienda</th><th>Frente al promedio</th><th>Productos comparados</th></tr>")
                for i, r in enumerate(ranking, 1):
                    diferencia = (r['indice'] - 1) * 100
                    clase = "price" if diferencia < 0 else "caro"
                    partes.append(f"<tr><td>{i}</td><td>{_e(r['tienda'])}</td>"
                                  f"<td class=\"{clase}\">{diferencia:+.0f}%</td><td>{r['productos']}</td></tr>")
                partes.append("</table></div>")

        # Biblioteca privada: lo que compró este usuario
        partes.append("<h2>📚 Mis compras</h2>")
        if not mis_precios:
            partes.append('<div class="vacio">Envía a Yoly una foto de tu factura de compra para empezar.</div>')
        else:
            por_producto = {}
            for p in mis_precios:
                por_producto.setdefault(p.get('producto_norm') or p.get('producto', 'otro'), []).append(p)
            partes.append("""<div class="tabla"><table><tr><th>Producto</th><th>Precio Min</th><th>Precio Max</th>
<th>Promedio</th><th>Tiendas</th><th>Compras</th></tr>""")
            for clave in sorted(por_producto):
                prods = por_producto[clave]
                lista = [precios.a_precio(p.get('precio')) or 0 for p in prods]
                tiendas = sorted({p.get('tienda', '') for p in prods})
                partes.append(f"""<tr><td><strong>{_e(prods[0].get('producto', clave))}</strong>
<div class="chico">{_e(prods[-1].get('medida', ''))}</div></td>
<td class="price">${min(lista):.2f}</td><td class="price">${max(lista):.2f}</td>
<td class="price">${sum(lista) / len(lista):.2f}</td><td class="chico">{_e(", ".join(tiendas))}</td>
<td>{len(prods)}</td></tr>""")
            partes.append("</table></div>")

        partes.append("</div></body></html>")
        return Response("".join(partes), mimetype="text/html")

    except Exception as e:
        logger.error(f"Error en dashboard de precios: {e}", exc_info=True)
        return "Error al cargar el comparador de precios", 500

# ==================== RESUMEN POR PERÍODO Y CARPETAS ====================

def periodo_de_request():
    """?desde=2026-06-01&hasta=2026-06-20; sin fechas = este mes."""
    try:
        inicio = datetime.strptime(request.args.get('desde', ''), '%Y-%m-%d').date()
        fin = datetime.strptime(request.args.get('hasta', ''), '%Y-%m-%d').date()
        if fin < inicio:
            inicio, fin = fin, inicio
        return {"inicio": inicio, "fin": fin, "etiqueta": reportes.etiqueta_periodo(inicio, fin)}
    except ValueError:
        return reportes.parsear_periodo('este mes')


@app.route("/dashboard/<phone>/periodo", methods=["GET"])
def dashboard_periodo(phone):
    """Ingresos, gastos, gráfico por categoría, semanas y movimientos entre dos fechas."""
    try:
        phone_clean = normalizar_telefono(phone)
        periodo = periodo_de_request()
        resumen, sin_fecha = resumen_periodo_usuario(phone_clean, periodo['inicio'], periodo['fin'])
        q = f"desde={periodo['inicio'].isoformat()}&hasta={periodo['fin'].isoformat()}"
        base = f"/dashboard/{quote_plus(phone_clean)}"

        partes = [f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Resumen {_e(periodo['etiqueta'])}</title>
<style>{DASHBOARD_PRECIOS_CSS}
.pos {{ color: #059669; }} .neg {{ color: #dc2626; }}
input[type=date] {{ padding: 6px; border-radius: 6px; border: 1px solid #d1d5db; }}
button {{ padding: 7px 14px; border-radius: 6px; border: 0; background: #1e40af; color: white; font-weight: bold; }}
.acciones a {{ margin: 4px 6px 4px 0; }}
.btn.verde {{ background: #16a34a; }} .btn.rojo {{ background: #dc2626; }}
</style></head><body><div class="container">
<p><a class="volver" href="{base}">← Volver al panel</a> · <a class="volver" href="{base}/carpetas">📁 Carpetas</a></p>
<h1>📅 Resumen del {_e(periodo['etiqueta'])}</h1>
<form method="get">Desde <input type="date" name="desde" value="{periodo['inicio'].isoformat()}">
 hasta <input type="date" name="hasta" value="{periodo['fin'].isoformat()}"> <button type="submit">Ver</button></form>
<div class="stats">
<div class="stat-card"><h3>Ingresos</h3><div class="value">{reportes.dinero(resumen['total_ingresos'])}</div></div>
<div class="stat-card"><h3>Gastos</h3><div class="value">{reportes.dinero(resumen['total_gastos'])}</div></div>
<div class="stat-card"><h3>Balance</h3><div class="value">{reportes.dinero(resumen['balance'])}</div></div>
</div>
<div class="acciones"><a class="btn verde" href="/download/periodo/{quote_plus(phone_clean)}/excel?{q}">📊 Excel del período</a>
<a class="btn rojo" href="/download/periodo/{quote_plus(phone_clean)}/pdf?{q}">📄 PDF del período</a></div>"""]

        if not resumen['movimientos']:
            partes.append('<p class="vacio">No hay gastos ni ingresos guardados en estas fechas.</p>')
        else:
            cats = resumen['gastos_por_categoria']
            if cats:
                partes.append("<h2>Gastos por categoría</h2>")
                maximo = cats[0][1]
                for cat, monto in cats:
                    ancho = max(4, int(100 * monto / maximo)) if maximo else 4
                    pct = 100 * monto / resumen['total_gastos'] if resumen['total_gastos'] else 0
                    partes.append(f'<div class="barra"><span class="nombre">{_e(cat.replace("_", " "))}</span>'
                                  f'<div class="fondo"><div class="relleno" style="width:{ancho}%">'
                                  f'{reportes.dinero(monto)} ({pct:.0f}%)</div></div></div>')
            partes.append('<h2>Por semana (lunes a domingo)</h2><div class="tabla"><table>'
                          '<tr><th>Semana</th><th>Ingresos</th><th>Gastos</th><th>Balance</th></tr>')
            for sem in resumen['semanas']:
                clase = 'pos' if sem['balance'] >= 0 else 'neg'
                partes.append(f"<tr><td>{sem['lunes'].strftime('%d/%m')} – {sem['domingo'].strftime('%d/%m/%Y')}</td>"
                              f"<td>{reportes.dinero(sem['ingresos'])}</td><td>{reportes.dinero(sem['gastos'])}</td>"
                              f"<td class='{clase}'>{reportes.dinero(sem['balance'])}</td></tr>")
            partes.append("</table></div>")
            partes.append(f"<h2>Movimientos ({len(resumen['movimientos'])})</h2><div class='tabla'><table>"
                          "<tr><th>Fecha</th><th>Tipo</th><th>Categoría</th><th>Descripción</th><th>Monto</th></tr>")
            for m in resumen['movimientos']:
                clase = 'pos' if m['tipo'] == 'ingreso' else 'neg'
                revisar = " ❓" if m.get('revisar') or m['categoria'] == reportes.CARPETA_REVISAR else ""
                partes.append(f"<tr><td>{m['fecha'].strftime('%d/%m/%Y')}</td><td>{_e(m['tipo'])}</td>"
                              f"<td>{_e(m['categoria'].replace('_', ' '))}{revisar}</td><td>{_e(m['descripcion'])}</td>"
                              f"<td class='{clase}'>{reportes.dinero(m['monto'])}</td></tr>")
            partes.append("</table></div>")
        if sin_fecha:
            partes.append(f"<p class='chico'>⚠️ {sin_fecha} registros sin fecha no entran en ningún período.</p>")
        partes.append("</div></body></html>")
        return "".join(partes)
    except Exception as e:
        logger.error(f"Error en dashboard de período: {e}", exc_info=True)
        return "Error al cargar el resumen", 500


@app.route("/download/periodo/<phone>/excel", methods=["GET"])
def descargar_excel_periodo(phone):
    if not HAS_OPENPYXL:
        return "Excel no disponible (falta openpyxl)", 501
    try:
        phone_clean = normalizar_telefono(phone)
        periodo = periodo_de_request()
        resumen, _ = resumen_periodo_usuario(phone_clean, periodo['inicio'], periodo['fin'])
        ruta = f"/tmp/periodo_{phone_clean}_{periodo['inicio']:%Y%m%d}_{periodo['fin']:%Y%m%d}_{threading.get_ident()}.xlsx"
        reportes.generar_excel(resumen, periodo, ruta)
        with open(ruta, 'rb') as f:
            datos = f.read()
        os.remove(ruta)
        nombre = f"resumen_{periodo['inicio']:%Y%m%d}_al_{periodo['fin']:%Y%m%d}.xlsx"
        return Response(datos, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={nombre}"})
    except Exception as e:
        logger.error(f"Error en Excel del período: {e}", exc_info=True)
        return "Error al generar el Excel", 500


@app.route("/download/periodo/<phone>/pdf", methods=["GET"])
def descargar_pdf_periodo(phone):
    try:
        phone_clean = normalizar_telefono(phone)
        periodo = periodo_de_request()
        resumen, _ = resumen_periodo_usuario(phone_clean, periodo['inicio'], periodo['fin'])
        ruta = f"/tmp/periodo_{phone_clean}_{periodo['inicio']:%Y%m%d}_{periodo['fin']:%Y%m%d}_{threading.get_ident()}.pdf"
        reportes.generar_pdf(resumen, periodo, ruta)
        with open(ruta, 'rb') as f:
            datos = f.read()
        os.remove(ruta)
        nombre = f"resumen_{periodo['inicio']:%Y%m%d}_al_{periodo['fin']:%Y%m%d}.pdf"
        return Response(datos, mimetype="application/pdf",
                        headers={"Content-Disposition": f"inline; filename={nombre}"})
    except Exception as e:
        logger.error(f"Error en PDF del período: {e}", exc_info=True)
        return "Error al generar el PDF", 500


@app.route("/dashboard/<phone>/carpetas", methods=["GET"])
def dashboard_carpetas(phone):
    """Carpetas por tipo (sueldo, renta, deuda, servicios, ingreso, compras, por revisar) con sus transacciones."""
    try:
        phone_clean = normalizar_telefono(phone)
        carpetas = reportes.cargar_carpetas(DATA_DIR, phone_clean)
        elegida = request.args.get('carpeta')
        base = f"/dashboard/{quote_plus(phone_clean)}"
        partes = [f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Mis carpetas</title>
<style>{DASHBOARD_PRECIOS_CSS}
.carpetas {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
.carpeta {{ display: block; text-decoration: none; color: #1f2937; background: #f9fafb; border: 2px solid #e5e7eb; border-radius: 10px; padding: 14px; }}
.carpeta.activa {{ border-color: #1e40af; background: #eef2ff; }}
.carpeta .icono {{ font-size: 28px; }} .carpeta .total {{ font-weight: bold; font-size: 18px; }}
</style></head><body><div class="container">
<p><a class="volver" href="{base}">← Volver al panel</a> · <a class="volver" href="{base}/periodo">📅 Resumen por fechas</a></p>
<h1>📁 Mis carpetas</h1>
<p class="sub">Las transferencias se guardan solas según su concepto. Si una cae en "Por revisar", escríbele a Yoly "es renta", "es sueldo"...</p>"""]
        if not carpetas:
            partes.append('<p class="vacio">Todavía no hay transferencias ni compras guardadas. Manda la foto de una transferencia por WhatsApp.</p>')
        else:
            partes.append('<div class="carpetas">')
            for nombre in reportes.TODAS_CARPETAS:
                if nombre not in carpetas:
                    continue
                lista = carpetas[nombre]
                total = sum(reportes._a_numero(t.get('monto')) for t in lista)
                activa = " activa" if nombre == elegida else ""
                partes.append(f'<a class="carpeta{activa}" href="?carpeta={nombre}"><div class="icono">{reportes.EMOJI_CARPETA.get(nombre, "📁")}</div>'
                              f'<div>{_e(NOMBRES_CARPETA.get(nombre, nombre))}</div><div class="total">{reportes.dinero(total)}</div>'
                              f'<div class="chico">{len(lista)} movimiento{"s" if len(lista) != 1 else ""}</div></a>')
            partes.append('</div>')
            mostrar = [elegida] if elegida in carpetas else [c for c in reportes.TODAS_CARPETAS if c in carpetas]
            for nombre in mostrar:
                partes.append(f"<h2>{reportes.EMOJI_CARPETA.get(nombre, '📁')} {_e(NOMBRES_CARPETA.get(nombre, nombre))}</h2>"
                              "<div class='tabla'><table><tr><th>Fecha</th><th>Descripción</th><th>Tipo</th><th>Monto</th></tr>")
                for t in reversed(carpetas[nombre]):
                    fecha = t.get('fecha') or ''
                    try:
                        fecha = datetime.strptime(fecha, '%Y-%m-%d').strftime('%d/%m/%Y')
                    except ValueError:
                        pass
                    marca = " ❓" if t.get('revisar') else ""
                    partes.append(f"<tr><td>{_e(fecha)}{marca}</td><td>{_e(t.get('descripcion', ''))}</td>"
                                  f"<td>{_e(t.get('movimiento', 'gasto'))}</td><td class='price'>{reportes.dinero(reportes._a_numero(t.get('monto')))}</td></tr>")
                partes.append("</table></div>")
        partes.append("</div></body></html>")
        return "".join(partes)
    except Exception as e:
        logger.error(f"Error en dashboard de carpetas: {e}", exc_info=True)
        return "Error al cargar las carpetas", 500


@app.route("/api/gastos/<phone>", methods=["GET"])
def api_gastos(phone):
    """API endpoint para obtener gastos en JSON"""
    try:
        gastos = cargar_gastos(phone)
        if not gastos:
            return jsonify([]), 200

        # Filtrar gastos del mes actual si se solicita
        filtro = request.args.get('filtro', 'todos')
        if filtro == 'mes':
            hoy = datetime.now()
            mes_actual = hoy.month
            anio_actual = hoy.year
            gastos = [g for g in gastos if 'fecha' in g and
                     datetime.strptime(g['fecha'], '%Y-%m-%d').month == mes_actual and
                     datetime.strptime(g['fecha'], '%Y-%m-%d').year == anio_actual]

        return jsonify(gastos), 200
    except Exception as e:
        logger.error(f"Error en API gastos: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/manifest.json", methods=["GET"])
def manifest():
    """PWA Manifest para instalación como app"""
    manifest_data = {
        "name": "Yoly Finanzas",
        "short_name": "Yoly",
        "description": "Control de gastos y finanzas USA-Ecuador",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": "#3B82F6",
        "background_color": "#ffffff",
        "orientation": "portrait-primary",
        "icons": [
            {
                "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E%3Crect fill='%233B82F6' width='192' height='192'/%3E%3Ctext x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-size='120' fill='white' font-family='Arial,sans-serif' font-weight='bold'%3EY%3C/text%3E%3C/svg%3E",
                "sizes": "192x192",
                "type": "image/svg+xml",
                "purpose": "any"
            }
        ],
        "screenshots": [
            {
                "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 540 720'%3E%3Crect fill='%233B82F6' width='540' height='720'/%3E%3Ctext x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-size='60' fill='white' font-family='Arial,sans-serif'%3EYoly%3C/text%3E%3C/svg%3E",
                "sizes": "540x720",
                "type": "image/svg+xml",
                "form_factor": "narrow"
            }
        ],
        "categories": ["finance", "productivity"]
    }
    return jsonify(manifest_data), 200, {"Content-Type": "application/manifest+json"}

@app.route("/sw.js", methods=["GET"])
def service_worker():
    """Service Worker para PWA - caching y offline support"""
    sw_code = """
const CACHE_NAME = 'yoly-v1';
const urlsToCache = [
  '/',
  '/manifest.json'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(urlsToCache);
    })
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(response => {
      if (response) {
        return response;
      }

      return fetch(event.request).then(response => {
        if (!response || response.status !== 200 || response.type !== 'basic') {
          return response;
        }

        const responseToCache = response.clone();
        caches.open(CACHE_NAME).then(cache => {
          cache.put(event.request, responseToCache);
        });

        return response;
      }).catch(() => {
        return caches.match('/');
      });
    })
  );
});
"""
    return sw_code, 200, {"Content-Type": "application/javascript"}

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
            # WhatsApp puede mandar varias fotos juntas (MediaUrl0, MediaUrl1...): van todas al Portero
            try:
                num_media = int(request.form.get('NumMedia', '1') or 1)
            except ValueError:
                num_media = 1
            media_urls = [request.form.get(f'MediaUrl{i}', '') for i in range(max(num_media, 1))
                          if request.form.get(f'MediaContentType{i}', media_content_type).startswith("image/")]
            return atender_con_imagen([u for u in media_urls if u] or [media_url_0], incoming_msg, from_number, server_url)
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


def atender_con_imagen(media_urls, incoming_msg, from_number, server_url):
    """Procesa una o varias imágenes (factura/recibo/libretita) con los 4 agentes"""
    if isinstance(media_urls, str):
        media_urls = [media_urls]
    media_url = media_urls[0]
    salida = Salida()
    estado = {"listo": False, "tarde": False}
    candado = threading.Lock()

    def trabajar():
        try:
            global temp_productos
            msg_lower = incoming_msg.lower() if incoming_msg else ""

            # Detectar si es una imagen de precios/comparación
            es_precio = any(keyword in msg_lower for keyword in ['precio', 'compara', 'donde es mas barato', 'dónde es más barato', 'ticket', 'mercado'])

            if es_precio:
                # Extraer productos con Vision
                productos_dict, error = extraer_productos_vision(media_url, from_number)

                if error:
                    salida.message(error)
                    return

                if not productos_dict or not productos_dict.get('productos'):
                    salida.message("No pude leer los precios de la imagen. Asegúrate que sea un ticket o factura clara con precios visibles.")
                    return

                # Almacenar temporalmente para confirmación
                temp_productos[from_number] = productos_dict

                # Mostrar resumen
                tienda = productos_dict.get('tienda', 'desconocida')
                msg = f"🧾 Encontré en tu ticket de *{tienda}*:\n\n"

                for p in productos_dict['productos'][:5]:  # Mostrar máximo 5
                    producto = p.get('producto', '')
                    precio = p.get('precio', 0)
                    msg += f"  • {producto}: ${precio:.2f}\n"

                if len(productos_dict['productos']) > 5:
                    msg += f"  ... y {len(productos_dict['productos']) - 5} más\n"

                msg += f"\n¿Lo guardo para comparar precios? Responde *SI* o *NO*"
                salida.message(msg)
                return

            # Si no es precio, usar el flujo normal de gastos
            # Procesar la imagen con visión
            info_foto = {}
            resultado = procesar_fotos_whatsapp(media_urls, from_number, incoming_msg or "", info_foto)
            salida.message(resultado)

            # Si hay texto adicional, validar antes de reclasificar. Solo si la foto se guardó como
            # UN gasto: en transferencias el texto ya eligió la carpeta, y una libretita no es un gasto.
            solo_un_gasto = info_foto.get('caminos') == ['facturas']
            if incoming_msg and incoming_msg.lower().strip() and solo_un_gasto:
                msg_lower = incoming_msg.lower()

                # VALIDACIÓN: Detectar si es una queja/comentario o un envio_ecuador
                # Palabras que indican queja/problema, NO reclasificar
                falso_positivo_keywords = ["link", "cuentas", "longizo", "equivoca", "error", "mal", "no", "problem", "falla", "bug", "ayuda"]
                es_queja = any(k in msg_lower for k in falso_positivo_keywords)

                # Palabras que realmente indican envio_ecuador
                envio_ecuador_keywords = ["ecuador", "envio", "giro", "remesa"]
                es_envio_ecuador = any(k in msg_lower for k in envio_ecuador_keywords)

                # Solo reclasificar si NO es una queja/comentario
                if not es_queja:
                    # Esperar un momento para que se guarde el gasto
                    import time
                    time.sleep(0.5)
                    resultado_reclasificacion = reclasificar_gasto(incoming_msg, from_number)
                    salida.message(resultado_reclasificacion)
                else:
                    # Es una queja o comentario, ignorar reclasificación automática
                    logger.info(f"Detected complaint/comment from {from_number}, skipping reclassification: {msg_lower}")
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


# ==================== PRICE LIBRARY & GLOBAL MARKET ====================

def extraer_productos_vision(media_url, telefono):
    """Usa Claude Vision para extraer productos de un ticket/recibo"""
    try:
        # Descargar imagen
        imagen_bytes = descargar_media_twilio(media_url)
        if not imagen_bytes:
            return None, "❌ No pude descargar la imagen. Intenta de nuevo."

        # Convertir a WEBP
        webp_bytes = convertir_a_webp(imagen_bytes)
        if not webp_bytes:
            return None, "❌ No pude procesar la imagen. Intenta con otra."

        # Convertir a base64
        imagen_base64 = base64.standard_b64encode(webp_bytes).decode('utf-8')

        # Llamar a Claude Vision
        response = client.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=800,
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
                        "text": """Analiza esta imagen de ticket/factura. Extrae SOLO un JSON válido, sin explicaciones:
{
  "tienda": "nombre de la tienda",
  "ciudad": "ciudad donde está la tienda",
  "productos": [
    {
      "producto": "nombre del producto",
      "producto_norm": "nombre normalizado a minusculas",
      "precio": número,
      "marca": "marca si aparece",
      "medida": "tamaño/cantidad",
      "categoria": "granos, aceites, bebidas, lacteos, etc"
    }
  ]
}

INSTRUCCION CRITICA:
- SOLO extrae números que REALMENTE aparecen en la factura
- Normaliza producto_norm a minusculas sin acentos
- Si no ves tienda, pon "desconocida"
- Si no ves ciudad, pon "no especificada"
- Si no ves marca, medida o categoria, deja vacío o "desconocido"
- NUNCA inventes precios"""
                    }
                ]
            }]
        )

        # Parsear respuesta
        try:
            texto_respuesta = response.content[0].text.strip()
            # Limpiar posibles marcas de código
            if texto_respuesta.startswith('```'):
                texto_respuesta = texto_respuesta.split('```')[1]
                if texto_respuesta.startswith('json'):
                    texto_respuesta = texto_respuesta[4:]
            if texto_respuesta.endswith('```'):
                texto_respuesta = texto_respuesta[:-3]

            productos_dict = json.loads(texto_respuesta)
            return productos_dict, None
        except json.JSONDecodeError as e:
            logger.error(f"Error parseando JSON de productos: {e}")
            return None, "❌ No pude procesar la imagen. Asegúrate que sea un ticket o factura clara."

    except Exception as e:
        logger.error(f"Error en extraer_productos_vision: {e}", exc_info=True)
        return None, f"❌ Error procesando imagen: {str(e)}"

def guardar_en_biblioteca(telefono, producto_dict, tienda, ciudad):
    """Guarda un producto en la biblioteca privada de precios del usuario"""
    try:
        phone_clean = normalizar_telefono(telefono)
        ruta = f"{DATA_DIR}/{phone_clean}/biblioteca_precios.json"

        # Crear directorio si no existe
        os.makedirs(os.path.dirname(ruta), exist_ok=True)

        # Leer documentos existentes
        docs = []
        if os.path.exists(ruta):
            try:
                with open(ruta, 'r', encoding='utf-8') as f:
                    docs = json.load(f)
            except:
                docs = []

        # Crear nuevo documento
        doc = {
            "id": len(docs) + 1,
            "phone": phone_clean,
            "producto": producto_dict.get('producto', ''),
            "producto_norm": producto_dict.get('producto_norm', ''),
            "precio": producto_dict.get('precio', 0),
            "tienda": tienda,
            "ciudad": ciudad,
            "marca": producto_dict.get('marca', ''),
            "medida": producto_dict.get('medida', ''),
            "categoria": producto_dict.get('categoria', ''),
            "medida_norm": producto_dict.get('medida_norm', ''),
            "fecha": datetime.now().isoformat()
        }

        docs.append(doc)

        # Guardar
        with open(ruta, 'w', encoding='utf-8') as f:
            json.dump(docs, f, indent=2, ensure_ascii=False)

        return True
    except Exception as e:
        logger.error(f"Error guardando en biblioteca: {e}", exc_info=True)
        return False

def registrar_precios_factura(telefono, tienda, ciudad_factura, articulos_raw, fecha_compra=None):
    """Guarda los productos de una factura de compra en el comparador de su ciudad
    y devuelve el mensaje de Yoly (dónde estaba más barato). "" si no hay productos."""
    phone_clean = normalizar_telefono(telefono)
    articulos_raw = articulos_raw if isinstance(articulos_raw, list) else []
    articulos = [a for a in (precios.limpiar_articulo(x) for x in articulos_raw) if a]
    if not articulos:
        return ""
    sin_precio = len(articulos_raw) - len(articulos)
    tienda = str(tienda or '').strip()
    if precios.sin_acentos(tienda).lower() in ('', 'desconocida', 'desconocido', 'null', 'none'):
        tienda = 'Desconocida'

    # Ciudad: la impresa en la factura; si no aparece, la del usuario
    ciudad = precios.normalizar_ciudad(ciudad_factura)[1] or precios.ciudad_usuario(DATA_DIR, phone_clean)
    if not ciudad:
        for a in articulos:
            guardar_en_biblioteca(phone_clean, a, tienda, '')
        precios.guardar_pendientes(DATA_DIR, phone_clean, tienda, articulos, fecha_compra)
        return (f"🛒 Leí {len(articulos)} productos de *{tienda}*. Para compararlos con otras tiendas "
                f"necesito tu ciudad: escríbeme *mi ciudad es Quito* (con tu ciudad).")
    if not precios.ciudad_usuario(DATA_DIR, phone_clean):
        precios.guardar_ciudad_usuario(DATA_DIR, phone_clean, ciudad)

    ahorros = precios.comparar_factura(DATA_DIR, ciudad, tienda, articulos)
    guardados = precios.agregar_muestras(DATA_DIR, ciudad, tienda, articulos, phone_clean, fecha_compra)
    if guardados:
        for a in articulos:
            guardar_en_biblioteca(phone_clean, a, tienda, ciudad)
    return precios.msg_factura(ciudad, tienda, guardados, ahorros, sin_precio)


def responder_comparador(consulta, telefono, server_url):
    """Comandos del comparador: fijar ciudad, buscar producto, comparar ciudades, ranking de tiendas."""
    phone_clean = normalizar_telefono(telefono)
    ciudad_mia = precios.ciudad_usuario(DATA_DIR, phone_clean)
    link = f"\n\n📊 {server_url}/dashboard/{phone_clean}/precios"

    if consulta['tipo'] == 'ciudad':
        nombre = precios.guardar_ciudad_usuario(DATA_DIR, phone_clean, consulta['ciudad'])
        if not nombre:
            return "No entendí la ciudad 🤔 Escríbeme por ejemplo: *mi ciudad es Quito*"
        texto = f"📍 Listo, tu ciudad es *{nombre}*. Comparo tus facturas con otras tiendas de {nombre}."
        for pendiente in precios.tomar_pendientes(DATA_DIR, phone_clean):
            ahorros = precios.comparar_factura(DATA_DIR, nombre, pendiente['tienda'], pendiente['articulos'])
            guardados = precios.agregar_muestras(DATA_DIR, nombre, pendiente['tienda'], pendiente['articulos'],
                                                 phone_clean, pendiente.get('fecha'))
            texto += "\n\n" + precios.msg_factura(nombre, pendiente['tienda'], guardados, ahorros)
        return texto

    ciudad = precios.normalizar_ciudad(consulta.get('ciudad'))[1] or ciudad_mia

    if consulta['tipo'] == 'ranking':
        if not ciudad:
            return "¿De qué ciudad? Escríbeme *mi ciudad es Quito* (con tu ciudad) o *tiendas baratas en Quito*."
        return precios.msg_ranking(ciudad, precios.ranking_tiendas(DATA_DIR, ciudad)) + link

    producto = consulta.get('producto', '')
    if len(producto) < 2:
        return "¿Qué producto quieres comparar? Ejemplo: *¿dónde es más barato el arroz 2kg?*"
    if consulta.get('todas'):
        return precios.msg_entre_ciudades(producto, precios.buscar_entre_ciudades(DATA_DIR, producto))
    if not ciudad:
        texto = precios.msg_entre_ciudades(producto, precios.buscar_entre_ciudades(DATA_DIR, producto))
        return texto + "\n\n📍 Para comparar solo en tu ciudad escríbeme *mi ciudad es Quito* (con tu ciudad)."
    filas = precios.buscar_en_ciudad(DATA_DIR, ciudad, producto)
    return precios.msg_buscar(producto, ciudad, filas) + (link if filas else "")

# ==================== CONTEXTO: RESPUESTA A "¿TE MANDO TABLA AL DASHBOARD?" ====================

PALABRAS_AFIRMATIVAS = {'si', 'sii', 'siii', 'sip', 'yes', 'ok', 'okay', 'okey', 'vale', 'dale', 'claro',
                        'correcto', 'listo', 'perfecto', 'bueno', 'va', 'porfa', 'porfavor', 'mandala',
                        'mandamela', 'enviala', 'envíala', 'mándala', 'mándamela', 'manda', 'envia',
                        'envía', 'quiero', 'obvio', 'simon', 'afirmativo', '👍', '👌', '✅'}

def sin_acentos(texto):
    return (texto.replace('á', 'a').replace('é', 'e').replace('í', 'i')
                 .replace('ó', 'o').replace('ú', 'u'))

def es_afirmativo(msg_lower):
    """True para "si", "Sí.", "si porfa", "sí mándala", "dale", "ok 👍", "si quiero la tabla"...
    False si el mensaje dice "no" o es largo (entonces no es solo una respuesta)."""
    texto = sin_acentos(msg_lower.strip())
    palabras = re.findall(r"[a-zñ]+|[👍👌✅]", texto)
    if not palabras or len(palabras) > 8 or 'no' in palabras:
        return False
    return palabras[0] in {sin_acentos(p) for p in PALABRAS_AFIRMATIVAS}

def obtener_ultima_pregunta(phone_clean):
    """Lee la última pregunta pendiente (memoria en RAM y, si no está, el archivo).
    Busca por los últimos 10 dígitos porque la clave puede venir con o sin prefijo."""
    ultimos10 = phone_clean[-10:]
    for memoria in (memoria_usuarios, cargar_memoria()):
        for clave, datos in memoria.items():
            if normalizar_telefono(clave)[-10:] == ultimos10 and isinstance(datos, dict):
                pregunta = (datos.get('ultima_pregunta') or '').lower()
                if pregunta:
                    return pregunta
    return ''

def limpiar_ultima_pregunta(phone_clean):
    ultimos10 = phone_clean[-10:]
    for clave, datos in memoria_usuarios.items():
        if normalizar_telefono(clave)[-10:] == ultimos10 and isinstance(datos, dict):
            datos['ultima_pregunta'] = ''
    guardar_memoria(memoria_usuarios)

def tiene_datos(phone_clean):
    try:
        return bool(obtener_cobro(phone_clean) or cargar_gastos(phone_clean))
    except Exception:
        return False

def mensaje_link_dashboard(server_url, phone_clean):
    return f"""✅ Perfecto, aquí está tu panel:
{server_url}/dashboard/{phone_clean}

📊 Descargas disponibles:
- Excel con todos tus pagos
- PDF con el reporte financiero

Los datos están listos para descargar."""

def opcion_numero(msg_lower):
    """Devuelve 1 o 2 si el usuario contestó con el número de la opción ("1", "1.", "1️⃣", "uno")."""
    texto = msg_lower.strip().rstrip('.!)').replace('\ufe0f', '').replace('\u20e3', '').strip()
    if texto in ('1', 'uno', 'opcion 1', 'opción 1'):
        return 1
    if texto in ('2', 'dos', 'opcion 2', 'opción 2'):
        return 2
    return None

def procesar_mensaje(incoming_msg, from_number, server_url, resp):
    """Arma la respuesta de Yoly. `resp` junta los textos (ver Salida)."""
    global temp_gastos, temp_productos, memoria_usuarios
    msg_lower = incoming_msg.lower()
    phone_clean = normalizar_telefono(from_number)

    try:
        # ==================== RESUMEN POR PERÍODO ====================
        # "resumen del 1 al 20 de junio", "gastos de junio", "balance de septiembre", "informe de gastos"
        # Va antes del balance para que "balance de septiembre" no se quede en la deuda.
        periodo = pedido_resumen_periodo(incoming_msg)
        if periodo:
            logger.info(f"Period report {periodo['inicio']}..{periodo['fin']} for {from_number}")
            responder(resp, responder_resumen_periodo(from_number, periodo, server_url))
            return

        # ==================== MOVER TRANSFERENCIA DE CARPETA ====================
        # "es renta", "ponlo en sueldo", "era deuda" después de mandar una transferencia
        carpeta_pedida = reportes.pedido_mover(incoming_msg)
        if carpeta_pedida:
            texto_mover = mover_transferencia(from_number, carpeta_pedida, server_url)
            if texto_mover:
                resp.message(texto_mover)
                return

        # ==================== INTENT DETECTION: BALANCE / DEUDA ====================
        # Palabras clave para detectar consulta de balance
        palabras_balance = ['balance', 'alan', 'debo', 'cuanto debo', 'cuanto falta', 'deuda', 'adeudo', 'que debo']
        tiene_intent_balance = any(palabra in msg_lower for palabra in palabras_balance)

        if tiene_intent_balance:
            cobro = obtener_cobro(phone_clean)
            if cobro:
                respuesta_balance = f"""💳 Según lo que registraste ({cobro['cliente']}):
Deuda: ${cobro['deuda']:,.0f}
Pagado ({len(cobro['pagos'])} pagos): ${cobro['pagado']:,.0f}
Te falta: ${cobro['saldo']:,.0f}

Link: {server_url}/dashboard/{phone_clean}"""

                resp.message(respuesta_balance)
                return

        # ==================== CONFIRMATION FLOW: SI/NO ====================
        # Check if user is confirming or rejecting a temporary expense
        confirmacion_palabras = ['si', 'yes', 'ok', 'vale', 'correcto', 'está bien', 'esta bien', 'ok!', 'si!', 'sí']
        rechazo_palabras = ['no', 'nope', 'incorrecto', 'de nuevo', 'de vueltas', 'otra vez']

        # CONFIRMACIÓN SI/NO - Chequear si es respuesta a "¿Te mando tabla al dashboard?"
        # Acepta "sí.", "si porfa", "dale", "mándala", "si quiero la tabla"... (antes solo "si" exacto,
        # y lo demás caía a Claude que respondía "¿qué necesitas?").
        # Si pide PDF o Excel, lo atiende el bloque de descargas de más abajo.
        hay_confirmacion_pendiente = from_number in temp_gastos or from_number in temp_productos

        # Respuesta con número a "¿Te mando tabla? 1 Sí / 2 No". No depende de la memoria
        # (que se pierde si Render reinicia): "1" siempre manda el link.
        opcion = opcion_numero(msg_lower)
        if opcion and not hay_confirmacion_pendiente:
            if opcion == 1:
                resp.message(mensaje_link_dashboard(server_url, phone_clean))
            else:
                resp.message("Listo 👍 Cuando quieras la tabla, escríbeme 1 o \"tabla\".")
            limpiar_ultima_pregunta(phone_clean)
            return
        pide_archivo = 'pdf' in msg_lower or 'excel' in msg_lower
        afirmativo = es_afirmativo(msg_lower)
        if afirmativo and not hay_confirmacion_pendiente and not pide_archivo:
            ultima_pregunta = obtener_ultima_pregunta(phone_clean)

            # PRIORIDAD 1: Respuesta a "¿dashboard?" / "¿tabla?"
            # Sin pregunta guardada (p. ej. Render reinició y se borró la memoria) pero con datos:
            # un "sí" suelto casi siempre es a la tabla, así que mandamos el link igual.
            if "dashboard" in ultima_pregunta or "tabla" in ultima_pregunta or (
                    not ultima_pregunta and tiene_datos(phone_clean)):
                resp.message(mensaje_link_dashboard(server_url, phone_clean))
                # Limpiar contexto para siguiente pregunta
                limpiar_ultima_pregunta(phone_clean)
                return

            # PRIORIDAD 2: Respuesta a "¿balance?" (código existente)
            elif "balance" in ultima_pregunta:
                cobro = obtener_cobro(phone_clean)
                if cobro:
                    respuesta_balance = f"""Perfecto.
Deuda original: ${cobro['deuda']:,.0f}
Pagado: ${cobro['pagado']:,.0f}
Te falta: ${cobro['saldo']:,.0f}

📊 Documentar aquí: {server_url}/dashboard/{phone_clean}"""
                    resp.message(respuesta_balance)
                    limpiar_ultima_pregunta(phone_clean)
                    return

        # "No" a "¿Te mando tabla al dashboard?": cerrar la pregunta en vez de caer a Claude
        if (msg_lower.strip().rstrip('.!') in rechazo_palabras and not hay_confirmacion_pendiente
                and "dashboard" in obtener_ultima_pregunta(phone_clean)):
            limpiar_ultima_pregunta(phone_clean)
            resp.message("Listo 👍 Cuando quieras la tabla, escríbeme 1 o \"tabla\".")
            return

        if msg_lower.strip() in confirmacion_palabras and from_number in temp_gastos:
            # User confirmed the expense!
            gasto_temp = temp_gastos[from_number]
            phone_clean = normalizar_telefono(from_number)
            gastos = cargar_gastos(phone_clean)
            timestamp_iso = datetime.now().isoformat()
            gasto_id = f"desglose_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            total = gasto_temp["total_enviado"]
            desglose = gasto_temp["desglose"]
            reserva = gasto_temp["reserva"]
            alerta = gasto_temp.get("alerta")

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
                "diferencia": gasto_temp["diferencia"],
                "validacion_alerta": alerta
            }

            gastos.append(gasto_nuevo)
            guardar_gastos(phone_clean, gastos)
            del temp_gastos[from_number]

            logger.info(f"Expense breakdown confirmed and saved for {from_number}: ${total}")

            # Send confirmation
            auto_respuesta = "¡Listo! ✅\n📤 Total: $" + f"{total:.2f}\n"
            for categoria, monto in desglose.items():
                auto_respuesta += f"  • {categoria.title()}: ${monto:.2f}\n"

            if reserva > 0:
                auto_respuesta += f"  • Reserva por si acaso: ${reserva:.2f}\n"
            elif reserva < 0:
                auto_respuesta += f"  • Sobregiro: ${abs(reserva):.2f} ⚠️\n"

            if alerta:
                auto_respuesta += f"\n{alerta}\n"

            dashboard_url = f"{server_url}/dashboard/{phone_clean}"
            auto_respuesta += f"\nAquí tienes tu panel: {dashboard_url}\n\n"
            auto_respuesta += "¿Quieres PDF del mes o con esto es suficiente?"

            resp.message(auto_respuesta)
            return

        elif msg_lower.strip() in rechazo_palabras and from_number in temp_gastos:
            # User rejected the expense
            del temp_gastos[from_number]
            resp.message("❌ Listo, cancelado. Dime de nuevo cómo es. Ejemplo: 'Envié 500, renta 380, comida 120'")
            return

        # ==================== CONFIRMATION FLOW FOR PRICE LIBRARY: SI/NO ====================
        # Check if user is confirming products from ticket photo
        if msg_lower.strip() in confirmacion_palabras and from_number in temp_productos:
            # User confirmed the products!
            prods_temp = temp_productos[from_number]
            phone_clean = normalizar_telefono(from_number)

            del temp_productos[from_number]
            texto = registrar_precios_factura(from_number, prods_temp.get('tienda'), prods_temp.get('ciudad'),
                                              prods_temp.get('productos', []))
            resp.message((texto or "No encontré productos con precio claro en ese ticket.") +
                         f"\n\n📊 Tus precios: {server_url}/dashboard/{phone_clean}/precios")
            return

        elif msg_lower.strip() in rechazo_palabras and from_number in temp_productos:
            # User rejected the products
            del temp_productos[from_number]
            resp.message("❌ Listo, cancelado. Envía otra foto del ticket.")
            return

        # ==================== DETECCIÓN DE PALABRAS CLAVE: PDF, EXCEL, LINK, PANEL, DASHBOARD ====================

        palabras_clave_link = ['pdf', 'excel', 'link', 'panel', 'dashboard', 'descargar', 'tabla']

        # "no quiero la tabla" no es pedir la tabla
        palabras_msg = re.findall(r"[a-zñáéíóú]+", msg_lower)
        if 'no' in palabras_msg and not any(k in msg_lower for k in palabras_clave_link if k != 'tabla'):
            palabras_clave_link = [k for k in palabras_clave_link if k != 'tabla']

        if any(keyword in msg_lower for keyword in palabras_clave_link):
            logger.info(f"Keyword detection for dashboard/downloads: {from_number}")

            # Generar respuesta rápida
            phone_clean = normalizar_telefono(from_number)
            if 'pdf' in msg_lower:
                try:
                    pdf_path = generar_pdf_dashboard(phone_clean)
                    if pdf_path and os.path.exists(pdf_path):
                        pdf_url = f"{server_url}/download/pdf/{phone_clean}"
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
                if not HAS_OPENPYXL:
                    resp.message("Excel no está disponible en este momento. Usa el PDF en su lugar.")
                    return
                try:
                    excel_path = generar_excel_gastos(phone_clean)
                    if excel_path and os.path.exists(excel_path):
                        excel_url = f"{server_url}/download/excel/{phone_clean}"
                        resp.message(f"Tu Excel está listo: {excel_url}")
                        return
                except Exception as e:
                    logger.error(f"Error generando Excel: {e}")

            # Si pide link, dashboard, panel o descargar
            if any(keyword in msg_lower for keyword in ['link', 'panel', 'dashboard', 'tabla']):
                phone_clean = normalizar_telefono(from_number)
                dashboard_url = f"{server_url}/dashboard/{phone_clean}"
                resp.message(f"Aquí está: {dashboard_url}\n\nTienes opciones para descargar Excel o PDF una vez ahí.")
                return

        # ==================== COMPARADOR DE PRECIOS POR CIUDAD ====================
        # "mi ciudad es Quito", "¿dónde es más barato el arroz?", "precio de aceite en todas las ciudades",
        # "tiendas baratas en Quito". Solo compara precios de la misma ciudad.
        consulta_precios = precios.parsear_consulta(DATA_DIR, incoming_msg)
        if consulta_precios:
            logger.info(f"Price comparison request from {from_number}: {consulta_precios}")
            resp.message(responder_comparador(consulta_precios, from_number, server_url))
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

            # STORE TEMPORARILY - Don't save yet!
            temp_gastos[from_number] = resultado
            logger.info(f"Expense breakdown stored temporarily for {from_number}: ${total}")

            # Ask for confirmation
            resumen = f"Entendí esto:\n📤 Total: ${total:.2f}\n\nDesglose:\n"
            for categoria, monto in desglose.items():
                resumen += f"  • {categoria.title()}: ${monto:.2f}\n"

            if reserva > 0:
                resumen += f"  • Reserva: ${reserva:.2f}\n"
            elif reserva < 0:
                resumen += f"  • Sobregiro: ${abs(reserva):.2f} ⚠️\n"

            if alerta:
                resumen += f"\n{alerta}\n"

            resumen += "\n¿Está bien? Responde **SI** o **NO**"

            resp.message(resumen)
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
