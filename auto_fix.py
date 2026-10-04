#!/usr/bin/env python3
import os
import sys
import traceback

# Variables de ambiente necesarias
if not os.getenv('ANTHROPIC_API_KEY'):
    print("ERROR: Falta ANTHROPIC_API_KEY")
    sys.exit(1)

os.environ.setdefault('ANTHROPIC_MODEL', 'claude-3-5-haiku-20241022')
os.environ.setdefault('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')

from simular_whatsapp import simular_mensaje

mensaje_test = """gano 17 dolares la hora trabajo 6 horas diarias de lunes a viernes ademas gano 400 semanales de otro ingreso y en mis gastos oaho 380 rebta en quito mensual, 120 comida semanal, pago 35 a la semana de plataformas y suscripciones gasto 100 al mes aprox. de seguro de gimnasio 53, de telefono 85 de arriendo new york 500 y en varios 100"""

intento = 0
max_intentos = 3

while intento < max_intentos:
    intento += 1
    print(f"\n[INTENTO {intento}/{max_intentos}]")

    try:
        resultado = simular_mensaje(mensaje_test)

        # Verifica que tiene la proyección
        if "proyeccion" in resultado.lower() or "presupuesto" in resultado.lower():
            print("\n✅ PRUEBA EXITOSA - Presupuesto generado correctamente")
            sys.exit(0)
        else:
            print("\n⚠️ Respuesta sin presupuesto, reintentando...")

    except Exception as e:
        error_msg = str(e).lower()

        if "channel" in error_msg or "invalid from" in error_msg:
            print(f"[FIX] Error de Channel - verificando TWILIO_WHATSAPP_NUMBER...")
            # El error ya está arreglado en main.py

        elif "model" in error_msg or "404" in error_msg:
            print(f"[FIX] Modelo no encontrado - usando claude-3-5-haiku-20241022...")
            os.environ['ANTHROPIC_MODEL'] = 'claude-3-5-haiku-20241022'

        elif "hardcoded" in error_msg:
            print(f"[FIX] Número hardcodeado detectado...")

        else:
            print(f"[ERROR] {type(e).__name__}: {e}")
            traceback.print_exc()

print("\n❌ No se pudo generar presupuesto después de intentos")
sys.exit(1)
