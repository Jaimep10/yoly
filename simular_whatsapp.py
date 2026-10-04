# Simula peticiones de Twilio desde WhatsApp sin enviar mensajes reales
import os
import sys
from unittest.mock import patch, MagicMock
from flask import Flask
from werkzeug.datastructures import ImmutableMultiDict

# Mockea Twilio client antes de importar main
mock_messages = MagicMock()

def mock_create(**kwargs):
    print(f"[MOCK TWILIO] FROM: {kwargs.get('from_')} TO: {kwargs.get('to')} BODY: {kwargs.get('body')}")
    return MagicMock(sid='test_message_id')

mock_messages.create = mock_create

def simular_mensaje(texto_usuario, telefono="whatsapp:+1234567890"):
    '''Simula un mensaje de WhatsApp sin usar Twilio real'''
    print(f"\n{'='*60}")
    print(f"[SIMULACIÓN] Usuario envía: {texto_usuario[:50]}...")
    print(f"[SIMULACIÓN] Desde: {telefono}")
    print(f"{'='*60}\n")

    # Importa main DESPUÉS de mockear
    from main import app, whatsapp

    # Mockea el cliente de Twilio
    with patch('main.client.messages', mock_messages):
        # Simula request.form de Twilio
        data = {
            'From': telefono,
            'To': os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886'),
            'Body': texto_usuario,
            'MessageSid': 'test_sid'
        }

        # Crea contexto Flask y simula POST
        with app.test_request_context('/', method='POST', data=data):
            try:
                resultado = whatsapp()
                print(f"\n[RESPUESTA] {resultado}\n")
                return resultado
            except Exception as e:
                print(f"\n[ERROR] {type(e).__name__}: {e}\n")
                raise
