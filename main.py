import os
os.environ["MPLBACKEND"] = "Agg"  # sin ventanas: los gráficos se dibujan fuera del hilo principal (Mac)
import json
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response
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
    'ANTHROPIC_API_KEY': 'Anthropic API Key'
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

if missing_vars:
    print(f"[WARNING] App will start but some features may not work. Missing: {', '.join(missing_vars)}")
    logger.warning(f"App starting with missing environment variables: {missing_vars}")
else:
    print("[OK] All required environment variables are configured")
    logger.info("All required environment variables are configured")

print("[STARTUP] Yoly Bot initialization complete")

GOALS_FILE = 'goals.json'

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

@app.route("/whatsapp", methods=["POST", "GET"])
def whatsapp():
    """
    WhatsApp webhook handler for Twilio.
    Processes messages for budget and goal management.
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

    print(f"[WHATSAPP] Received request from Twilio")
    print(f"[WHATSAPP] Extracted - From: {from_number}, MessageSID: {message_sid}, Body: {incoming_msg}")
    logger.info(f"[WHATSAPP] Message received - From: {from_number}, Body: {incoming_msg[:100]}")

    print(f"Pregunta recibida: {len(incoming_msg)} caracteres")

    if not incoming_msg:
        logger.warning(f"Empty message body received from {from_number}")
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
    print(f"[WHATSAPP MESSAGE] From: {from_number} | SID: {message_sid} | Body: {incoming_msg[:100]}")
    logger.info(f"Message received | From: {from_number} | MessageSID: {message_sid} | Body: {incoming_msg}")

    server_url = os.environ.get('SERVER_URL', request.host_url.rstrip('/'))
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


def procesar_mensaje(incoming_msg, from_number, server_url, resp):
    """Arma la respuesta de Yoly. `resp` junta los textos (ver Salida)."""
    msg_lower = incoming_msg.lower()

    try:
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
💰 Monto: ¿Cuánto necesitas?
📅 Plazo: ¿Para cuándo? (ej: 3 meses, 31/12/2024)
📂 Tipo: deuda, ahorro o inversión

Ejemplo: "Meta: Fondo emergencia, $3000, 3 meses, ahorro"
"""
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
