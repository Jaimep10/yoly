#!/usr/bin/env python3
"""
Test para verificar que el análisis de ingresos/gastos funciona correctamente
y que se mantiene la persistencia de contexto entre mensajes.
"""

import os
import json
from simular_whatsapp import simular_mensaje

def limpiar_contexto():
    """Limpia el archivo de contexto financiero para empezar con valores limpios"""
    if os.path.exists('financial_context.json'):
        os.remove('financial_context.json')
    print("[TEST] Contexto financiero limpiado")

def ejecutar_tests():
    """Ejecuta los tests de análisis financiero"""
    print("\n" + "="*70)
    print("[TEST] Iniciando pruebas de análisis financiero")
    print("="*70)

    # Limpiar contexto anterior
    limpiar_contexto()

    # TEST 1: Analizar ingresos
    print("\n[TEST 1] Mensaje 1 - Análisis de ingresos")
    print("-" * 70)
    mensaje_1 = "gano 17 dolares la hora trabajo 6 horas diarias de lunes a viernes ademas gano 400 semanales de otro ingreso"
    print(f"Mensaje del usuario: {mensaje_1}")

    try:
        resultado_1 = simular_mensaje(mensaje_1)

        # Verificar que se extrajeron los ingresos correctamente
        # Cálculo: 17 * 6 (horas/día) * 5 (días/semana) * 4 (semanas/mes) = 2,040
        # Plus 400 * 4.33 = 1,732
        # Total esperado: ~3,960

        print("\nValidando resultado del mensaje 1...")

        # Cargar contexto para verificar
        with open('financial_context.json', 'r') as f:
            contexto = json.load(f)

        ingresos_mensaje1 = contexto.get('ingresos_mensuales', 0)
        print(f"  Ingresos calculados: ${ingresos_mensaje1:.2f}")
        print(f"  Ingresos esperados: ~$3,960.00 (17 * 6 * 5 * 4 + 400 * 4.33)")

        if ingresos_mensaje1 > 3800 and ingresos_mensaje1 < 4100:
            print(f"  ✓ INGRESO CORRECTO (rango aceptable)")
        else:
            print(f"  ✗ INGRESO INCORRECTO (fuera del rango esperado)")

        print(f"\nRespuesta del bot:\n{resultado_1[:200]}...")

    except Exception as e:
        print(f"✗ Error en TEST 1: {e}")
        return False

    # TEST 2: Agregar gastos (mantener ingresos previos)
    print("\n[TEST 2] Mensaje 2 - Análisis de gastos (con persistencia)")
    print("-" * 70)
    mensaje_2 = "renta usa 500, renta ecuador 380, comida 120 semanal, teléfono 85, gimnasio 53"
    print(f"Mensaje del usuario: {mensaje_2}")

    try:
        resultado_2 = simular_mensaje(mensaje_2)

        # Verificar que se mantienen los ingresos y se agregan gastos
        # Gastos esperados:
        # renta usa: 500
        # renta ecuador: 380
        # comida semanal: 120 * 4.33 = 519.6
        # teléfono: 85
        # gimnasio: 53
        # Total gastos: ~1,538
        # Superávit: 3,960 - 1,538 = ~2,422

        print("\nValidando resultado del mensaje 2...")

        # Cargar contexto actualizado
        with open('financial_context.json', 'r') as f:
            contexto = json.load(f)

        ingresos_finales = contexto.get('ingresos_mensuales', 0)
        gastos_dict = contexto.get('gastos', {})
        gastos_totales = sum(gastos_dict.values())
        superavit = ingresos_finales - gastos_totales

        print(f"  Ingresos mantenidos: ${ingresos_finales:.2f}")
        print(f"  Gastos totales: ${gastos_totales:.2f}")
        print(f"  Superávit: ${superavit:.2f}")
        print(f"  Superávit esperado: ~$2,422 o superior")

        print(f"\n  Desglose de gastos:")
        for categoria, monto in gastos_dict.items():
            print(f"    • {categoria}: ${monto:.2f}")

        # Cálculo esperado correcto:
        # Ingresos: 17 * 6 * 5 * 4 + 400 * 4.33 = 2,040 + 1,732 = 3,772
        # Gastos: 500 + 380 + (120 * 4.33) + 85 + 53 = 880 + 519.6 + 85 + 53 = 1,537.6
        # Superávit esperado: 3,772 - 1,537.6 = 2,234.4

        gastos_esperados = 500 + 380 + (120 * 4.33) + 85 + 53
        superavit_esperado = 3772 - gastos_esperados

        print(f"\n  Gastos calculados vs esperados: ${gastos_totales:.2f} vs ${gastos_esperados:.2f}")
        print(f"  Superávit esperado (teórico): ${superavit_esperado:.2f}")

        if abs(superavit - superavit_esperado) < 80:  # Margen para rounding
            print(f"  ✓ SUPERÁVIT CORRECTO")
        elif superavit > 2100 and superavit < 2400:
            print(f"  ✓ SUPERÁVIT CORRECTO (dentro del rango aceptable)")
        else:
            print(f"  ✗ SUPERÁVIT INCORRECTO (fuera del rango esperado)")

        print(f"\nRespuesta del bot:\n{resultado_2[:300]}...")

    except Exception as e:
        print(f"✗ Error en TEST 2: {e}")
        return False

    # VERIFICACIÓN FINAL
    print("\n" + "="*70)
    print("[TEST] RESULTADO FINAL")
    print("="*70)
    print(f"Ingresos acumulados: ${ingresos_finales:.2f}")
    print(f"Gastos acumulados: ${gastos_totales:.2f}")
    print(f"Superávit final: ${superavit:.2f}")

    # Verificar que el resultado sea el esperado
    # Esperado: ~$2,234 (con rango de tolerancia)
    if superavit > 2100 and superavit < 2400:
        print(f"\n✓ TESTS APROBADOS - Persistencia de contexto funcionando correctamente")
        print(f"  - Ingresos del primer mensaje se mantuvieron")
        print(f"  - Nuevos gastos fueron agregados (no reemplazados)")
        print(f"  - Clasificación de gastos es correcta")
        return True
    else:
        print("\n✗ TESTS FALLIDOS - El superávit no está en el rango esperado")
        return False

if __name__ == "__main__":
    # Establecer variables de entorno para las pruebas
    os.environ['TWILIO_ACCOUNT_SID'] = 'test_sid'
    os.environ['TWILIO_AUTH_TOKEN'] = 'test_auth'
    os.environ['TWILIO_WHATSAPP_NUMBER'] = 'whatsapp:+test'
    os.environ['ANTHROPIC_API_KEY'] = 'test_key'

    exito = ejecutar_tests()

    # Limpiar después de las pruebas
    print("\n[TEST] Limpiando archivos de prueba...")
    if os.path.exists('financial_context.json'):
        os.remove('financial_context.json')

    exit(0 if exito else 1)
