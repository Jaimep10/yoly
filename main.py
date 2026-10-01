import os
from flask import Flask, request, send_file
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import anthropic
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

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

    # Respuesta normal con Claude
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": f"Eres Yoly, un asistente virtual amable, útil, que responde corto y en español: {incoming_msg}"}]
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
