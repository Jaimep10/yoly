import os
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return "Yoly Bot Running OK"

@app.route("/webhook/whatsapp", methods=["POST", "GET"])
def webhook():
    incoming_msg = request.values.get("Body", "").strip()

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "Eres Yoly, un asistente virtual amable, útil, que responde corto y en español."
            },
            {
                "role": "user",
                "content": incoming_msg
            }
        ]
    )

    bot_response = response.choices[0].message.content

    resp = MessagingResponse()
    resp.message(bot_response)
    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
