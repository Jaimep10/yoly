import os
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, request, send_file
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
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
twilio_client = Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))

# Logging configuration
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

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
            model="claude-3-5-sonnet-20241022",
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
    c = canvas.Canvas(pdf_path, pagesize=letter)
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
    return pdf_path

def generar_informe_gastos():
    """Genera un PDF con gráfico de gastos de ejemplo"""
    pdf_path = '/tmp/informe_gastos.pdf'
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
    c = canvas.Canvas(pdf_path, pagesize=letter)
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
    return pdf_path

def generar_presupuesto():
    """Genera un PDF profesional con análisis de presupuesto mensual y asesoramiento financiero"""
    pdf_path = '/tmp/presupuesto_analisis.pdf'
    chart_path_pie = '/tmp/presupuesto_pie.png'
    chart_path_bar = '/tmp/presupuesto_bar.png'

    # Datos del presupuesto
    income = 3640
    fixed_expenses = {
        "Rent": 600,
        "Food": 480,
        "Gym": 53,
        "Phone": 85,
        "Subscriptions": 100
    }
    debt_expenses = {
        "Collaborator Debt": 120,
        "Debt-eciador": 380,
        "Debt-César": 300,
        "Debt-Yoly": 300,
        "Debt-Jenny": 200
    }

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
    plt.ylim(0, 4000)

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
    doc = SimpleDocTemplate(pdf_path, pagesize=letter)
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
            model="claude-3-5-sonnet-20241022",
            max_tokens=800,
            system="Eres un asesor financiero profesional. Proporciona análisis financiero detallado pero conciso en español.",
            messages=[{"role": "user", "content": advisor_message}]
        )
        advice_text = advisor_response.content[0].text
    except Exception as e:
        advice_text = f"Unable to generate advice: {str(e)}"

    story.append(Paragraph(advice_text, styles['Normal']))
    story.append(Spacer(1, 0.2*inch))

    # Financial Health Score
    story.append(Paragraph("Financial Health Summary", styles['Heading3']))
    summary_text = f"""<b>Monthly Surplus:</b> ${surplus:.2f}<br/>
    <b>Debt-to-Income Ratio:</b> {(total_debt/income)*100:.1f}%<br/>
    <b>Savings Rate:</b> {(surplus/income)*100:.1f}%<br/>
    <b>Overall Health:</b> Excellent - Strong surplus for debt payoff and emergency savings
    """
    story.append(Paragraph(summary_text, styles['Normal']))

    # Footer
    story.append(Spacer(1, 0.3*inch))
    footer_text = f"Report generated on {datetime.now().strftime('%B %d, %Y')} | Yoly Financial Advisor"
    story.append(Paragraph(footer_text, styles['Normal']))

    # Build PDF
    doc.build(story)

    # Cleanup chart files
    try:
        os.remove(chart_path_pie)
        os.remove(chart_path_bar)
    except:
        pass

    return pdf_path

@app.route("/", methods=["GET"])
def home():
    return "Yoly Bot Running OK"

@app.route("/download/informe_gastos.pdf", methods=["GET"])
def download_informe():
    """Sirve el PDF de informe de gastos"""
    pdf_path = '/tmp/informe_gastos.pdf'
    if os.path.exists(pdf_path):
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True, download_name='informe_gastos.pdf')
    return "Informe no encontrado", 404

@app.route("/download/presupuesto_analisis.pdf", methods=["GET"])
def download_presupuesto():
    """Sirve el PDF de análisis de presupuesto"""
    pdf_path = '/tmp/presupuesto_analisis.pdf'
    if os.path.exists(pdf_path):
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True, download_name='presupuesto_analisis.pdf')
    return "Presupuesto no encontrado", 404

@app.route("/download/informe_metas.pdf", methods=["GET"])
def download_informe_metas():
    """Sirve el PDF de informe de metas"""
    pdf_path = generar_informe_metas()
    if pdf_path and os.path.exists(pdf_path):
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True, download_name='informe_metas.pdf')
    return "Informe de metas no disponible", 404

@app.route("/whatsapp", methods=["POST", "GET"])
def whatsapp():
    """
    WhatsApp webhook handler with Twilio signature validation.
    Validates incoming requests and processes messages for budget and goal management.
    """
    logger.info("Webhook request received")

    # ==================== REQUEST VALIDATION ====================

    # Get Twilio credentials for signature validation
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not auth_token:
        logger.error("TWILIO_AUTH_TOKEN not configured")
        return "Error: Missing Twilio credentials", 500

    # Validate Twilio signature
    validator = RequestValidator(auth_token)
    twilio_signature = request.headers.get('X-Twilio-Signature', '')

    # Build URL for signature validation
    request_url = request.url
    post_data = request.values if request.method == 'POST' else {}

    # Validate signature
    if not validator.validate(request_url, post_data, twilio_signature):
        logger.warning(f"Invalid Twilio signature: {twilio_signature}")
        logger.warning(f"Request URL: {request_url}")
        logger.warning(f"POST data: {post_data}")
        resp = MessagingResponse()
        resp.message("❌ Validación fallida: Firma Twilio inválida")
        return str(resp), 403

    logger.info("Twilio signature validated successfully")

    # ==================== EXTRACT MESSAGE DATA ====================

    # Handle missing required fields
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    message_sid = request.values.get('MessageSid', 'unknown')
    account_sid = request.values.get('AccountSid', 'unknown')

    if not incoming_msg:
        logger.warning(f"Empty message body received from {from_number}")
        resp = MessagingResponse()
        resp.message("❌ Error: Mensaje vacío recibido")
        return str(resp), 400

    if not from_number:
        logger.error("Missing 'From' field in request")
        resp = MessagingResponse()
        resp.message("❌ Error: No se pudo identificar el remitente")
        return str(resp), 400

    # Log incoming message details
    logger.info(f"Message received | From: {from_number} | MessageSID: {message_sid} | Body: {incoming_msg}")

    # Initialize response
    resp = MessagingResponse()
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
                return str(resp)

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
            return str(resp)

        # Solicitar consejos y análisis de metas
        if any(keyword in msg_lower for keyword in ['consejo', 'consejos metas', 'analiza metas', 'tips', 'motivación']):
            logger.info(f"Financial advice request from {from_number}")
            consejo = analizar_metas_y_dar_consejos()
            resp.message(consejo)
            return str(resp)

        # Ver progreso de una meta específica
        if 'progreso' in msg_lower or 'avance' in msg_lower:
            logger.info(f"Progress check requested by {from_number}")
            metas = obtener_metas()
            if not metas:
                resp.message("No tienes metas. Crea una para empezar.")
                return str(resp)

            resumen_progreso = "📈 *Progreso de Metas:*\n"
            for meta in metas:
                barra_progreso = "█" * int(meta['porcentaje_progreso'] / 10) + "░" * (10 - int(meta['porcentaje_progreso'] / 10))
                resumen_progreso += f"\n{meta['nombre']}\n[{barra_progreso}] {meta['porcentaje_progreso']:.1f}%\n"

            logger.info(f"Progress report sent to {from_number}")
            resp.message(resumen_progreso)
            return str(resp)

        # Descargar informe de metas
        if 'informe metas' in msg_lower or 'reporte metas' in msg_lower:
            logger.info(f"Goals report request from {from_number}")
            try:
                pdf_path = generar_informe_metas()
                if pdf_path and os.path.exists(pdf_path):
                    server_url = os.environ.get('SERVER_URL', request.host_url.rstrip('/'))
                    pdf_url = f"{server_url}/download/informe_metas.pdf"
                    logger.info(f"Goals report generated: {pdf_url}")

                    twilio_client.messages.create(
                        from_="whatsapp:+14155552671",
                        to=from_number,
                        body="📊 Aquí está tu informe de metas financieras:",
                        media_url=[pdf_url]
                    )
                    logger.info(f"Goals report sent to {from_number}")
                    resp.message("Informe de metas enviado ✓")
                    return str(resp)
                else:
                    logger.warning(f"Goals report generation failed for {from_number}")
                    resp.message("No tienes metas para generar el informe. ¡Crea algunas!")
                    return str(resp)
            except Exception as e:
                logger.error(f"Error generando informe de metas: {e}", exc_info=True)
                resp.message(f"Error al generar informe: {str(e)}")
                return str(resp)

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
            return str(resp)

        # Actualizar progreso de meta
        if 'actualizar' in msg_lower or 'ahorré' in msg_lower or 'pagué' in msg_lower:
            logger.info(f"Goal progress update request from {from_number}")
            metas = obtener_metas()
            if not metas:
                resp.message("No tienes metas. Crea una primero.")
                return str(resp)

            respuesta = "¿Cuál meta actualizaste? Dime el nombre:\n"
            for meta in metas:
                respuesta += f"\n• {meta['nombre']}"
            resp.message(respuesta)
            return str(resp)

        # ==================== BUDGET & EXPENSE KEYWORDS ====================

        # Detectar si el usuario pide "informe de gastos"
        if 'informe de gastos' in msg_lower:
            logger.info(f"Expense report request from {from_number}")
            try:
                generar_informe_gastos()
                server_url = os.environ.get('SERVER_URL', request.host_url.rstrip('/'))
                pdf_url = f"{server_url}/download/informe_gastos.pdf"
                logger.info(f"Expense report generated: {pdf_url}")

                twilio_client.messages.create(
                    from_="whatsapp:+14155552671",
                    to=from_number,
                    body="Aquí está tu informe de gastos:",
                    media_url=[pdf_url]
                )
                logger.info(f"Expense report sent to {from_number}")
                resp.message("Informe de gastos enviado. Descárgalo desde el enlace.")
                return str(resp)
            except Exception as e:
                logger.error(f"Error generando informe de gastos: {e}", exc_info=True)
                resp.message(f"Error al generar informe: {str(e)}")
                return str(resp)

        # Detectar palabras clave para asesor financiero
        financial_keywords = ['presupuesto', 'gastos', 'asesor', 'ahorro']
        should_generate_budget = any(keyword in incoming_msg.lower() for keyword in financial_keywords)

        if should_generate_budget:
            logger.info(f"Budget analysis request from {from_number}")
            try:
                logger.debug("Generating budget analysis...")
                generar_presupuesto()

                server_url = os.environ.get('SERVER_URL', request.host_url.rstrip('/'))
                pdf_url = f"{server_url}/download/presupuesto_analisis.pdf"
                logger.info(f"Budget analysis generated: {pdf_url}")

                twilio_client.messages.create(
                    from_="whatsapp:+14155552671",
                    to=from_number,
                    body="📊 Aquí está tu análisis de presupuesto mensual con recomendaciones del asesor financiero de IA:",
                    media_url=[pdf_url]
                )

                logger.info(f"Budget analysis sent to {from_number}")
                resp = MessagingResponse()
                resp.message("✅ Reporte de presupuesto enviado. Incluye tu análisis financiero y recomendaciones personalizadas.")
                return str(resp)
            except Exception as e:
                logger.error(f"Error generando presupuesto: {e}", exc_info=True)
                resp = MessagingResponse()
                resp.message(f"❌ Error al generar presupuesto: {str(e)}")
                return str(resp)

        # ==================== DEFAULT RESPONSE ====================

        # Respuesta normal con Claude
        logger.info(f"Processing message with Claude API for {from_number}")
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            system="Eres Yoly, un asistente virtual amable, útil, que responde corto y en español. Eres especialista en finanzas personales y ayudas a tus usuarios a gestionar sus metas financieras.",
            messages=[{"role": "user", "content": incoming_msg}]
        )
        bot_response = response.content[0].text
        logger.info(f"Claude response generated for {from_number}")

    except Exception as e:
        logger.error(f"Unexpected error in webhook: {e}", exc_info=True)
        bot_response = f"Disculpa, hubo un error procesando tu mensaje: {str(e)}"

    resp.message(bot_response)
    logger.info(f"Response sent to {from_number}")
    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
