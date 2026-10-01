import os
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import anthropic

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

@app.route("/", methods=["GET"])
def health():
    return "Yoly Bot Running OK"

@app.route("/webhook/whatsapp", methods=["POST", "GET"])
def webhook():
    incoming_msg = request.values.get('Body', '').strip()
    print(f"Mensaje: {incoming_msg}")
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": f"Eres Yoly, asistente de Jaime Estrella, responde corto y amigable: {incoming_msg}"}]
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
