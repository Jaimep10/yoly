import os
from flask import Flask, request, send_file
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
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
def health():
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

@app.route("/webhook/whatsapp", methods=["POST", "GET"])
def webhook():
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    print(f"Mensaje: {incoming_msg}")

    # Detectar si el usuario pide "informe de gastos"
    if 'informe de gastos' in incoming_msg.lower():
        try:
            # Generar PDF con gráfico
            generar_informe_gastos()

            # Construir URL del PDF
            server_url = os.environ.get('SERVER_URL', request.host_url.rstrip('/'))
            pdf_url = f"{server_url}/download/informe_gastos.pdf"

            # Enviar por Twilio/WhatsApp
            twilio_client.messages.create(
                from_="whatsapp:+14155552671",  # Número de Twilio (sandbox)
                to=from_number,
                body="Aquí está tu informe de gastos:",
                media_url=[pdf_url]
            )

            resp = MessagingResponse()
            resp.message("Informe de gastos enviado. Descárgalo desde el enlace.")
            return str(resp)
        except Exception as e:
            print(f"Error generando informe: {e}")
            resp = MessagingResponse()
            resp.message(f"Error al generar informe: {str(e)}")
            return str(resp)

    # Detectar palabras clave para asesor financiero: presupuesto, gastos, asesor, ahorro
    financial_keywords = ['presupuesto', 'gastos', 'asesor', 'ahorro']
    should_generate_budget = any(keyword in incoming_msg.lower() for keyword in financial_keywords)

    if should_generate_budget:
        try:
            print("Generando análisis de presupuesto...")
            # Generar PDF con análisis de presupuesto y asesoramiento financiero
            generar_presupuesto()

            # Construir URL del PDF
            server_url = os.environ.get('SERVER_URL', request.host_url.rstrip('/'))
            pdf_url = f"{server_url}/download/presupuesto_analisis.pdf"

            # Enviar por Twilio/WhatsApp
            twilio_client.messages.create(
                from_="whatsapp:+14155552671",  # Número de Twilio (sandbox)
                to=from_number,
                body="📊 Aquí está tu análisis de presupuesto mensual con recomendaciones del asesor financiero de IA:",
                media_url=[pdf_url]
            )

            resp = MessagingResponse()
            resp.message("✅ Reporte de presupuesto enviado. Incluye tu análisis financiero y recomendaciones personalizadas.")
            return str(resp)
        except Exception as e:
            print(f"Error generando presupuesto: {e}")
            resp = MessagingResponse()
            resp.message(f"❌ Error al generar presupuesto: {str(e)}")
            return str(resp)

    # Respuesta normal con Claude
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            system="Eres Yoly, un asistente virtual amable, útil, que responde corto y en español.",
            messages=[{"role": "user", "content": incoming_msg}]
        )
        bot_response = response.content[0].text
    except Exception as e:
        print(f"Error: {e}")
        bot_response = f"Error: {e}"
    resp = MessagingResponse()
    resp.message(bot_response)
    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
