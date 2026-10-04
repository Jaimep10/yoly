#!/usr/bin/env python3
"""
Test suite para la función extraer_gastos()
Valida la lógica de desglose de gastos y cálculo de reserva
"""

import sys
import json
import re
import logging

# Mock logging para evitar importar main
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Definir extraer_gastos inline para evitar dependencias
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
            # Patron 1: numero seguido de categoria (ej: "380 renta")
            patron1 = rf'(\d+(?:\.\d+)?)\s+{categoria}'
            match1 = re.search(patron1, transcripcion_lower)

            # Patron 2: categoria seguida de numero (ej: "renta 380")
            patron2 = rf'{categoria}\s+\$?(\d+(?:\.\d+)?)'
            match2 = re.search(patron2, transcripcion_lower)

            # Patron 3: numero para categoria (ej: "380 para renta")
            patron3 = rf'(\d+(?:\.\d+)?)\s+(?:para|de)\s+{categoria}'
            match3 = re.search(patron3, transcripcion_lower)

            match = match1 or match2 or match3
            if match:
                try:
                    # Obtener el numero correcto segun cual match funciono
                    if match1:
                        monto = float(match1.group(1))
                    elif match2:
                        monto = float(match2.group(1))
                    else:
                        monto = float(match3.group(1))

                    # Normalizar nombre de categoria
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

def test_caso_usuario():
    """
    Caso de uso principal: Usuario dice "Envié 500, 380 renta, 120 comida, 30 Estefanito y resto reserva"
    """
    print("\n" + "="*70)
    print("TEST 1: Caso de usuario - Envié 500, 380 renta, 120 comida, 30 Estefanito")
    print("="*70)

    mensaje = "Envié 500, 380 renta, 120 comida, 30 Estefanito y resto reserva"
    resultado = extraer_gastos(mensaje)

    print(f"\nMensaje: {mensaje}")
    print(f"\nResultado:\n{json.dumps(resultado, indent=2)}")

    # Validaciones
    assert resultado["total_enviado"] == 500, f"Total debería ser 500, obtuvo {resultado['total_enviado']}"
    assert resultado["desglose"]["renta"] == 380, f"Renta debería ser 380"
    assert resultado["desglose"]["comida"] == 120, f"Comida debería ser 120"
    assert resultado["desglose"]["estefanito"] == 30, f"Estefanito debería ser 30"
    assert resultado["reserva"] == -30, f"Reserva debería ser -30 (se pasó)"
    assert resultado["valido"] == False, "Debería no ser válido (se pasó)"
    assert resultado["alerta"] is not None, "Debería tener alerta"

    print("\n✓ TEST PASÓ")
    return True

def test_sin_pasarse():
    """
    Caso: Usuario dice "Envié 530, 380 renta, 120 comida, 30 Estefanito"
    Suma = 530, esperado = 530, reserva = 0
    """
    print("\n" + "="*70)
    print("TEST 2: Sin pasarse - Envié 530, 380 renta, 120 comida, 30 Estefanito")
    print("="*70)

    mensaje = "Envié 530, 380 renta, 120 comida, 30 Estefanito"
    resultado = extraer_gastos(mensaje)

    print(f"\nMensaje: {mensaje}")
    print(f"\nResultado:\n{json.dumps(resultado, indent=2)}")

    assert resultado["total_enviado"] == 530
    assert resultado["reserva"] == 0, f"Reserva debería ser 0, obtuvo {resultado['reserva']}"
    assert resultado["valido"] == True, "Debería ser válido"
    assert resultado["alerta"] is None, "No debería tener alerta"

    print("\n✓ TEST PASÓ")
    return True

def test_con_reserva():
    """
    Caso: Usuario dice "Envié 600, 380 renta, 120 comida, 30 Estefanito"
    Suma = 530, total = 600, reserva = 70
    """
    print("\n" + "="*70)
    print("TEST 3: Con reserva positiva - Envié 600, 380 renta, 120 comida, 30 Estefanito")
    print("="*70)

    mensaje = "Envié 600, 380 renta, 120 comida, 30 Estefanito"
    resultado = extraer_gastos(mensaje)

    print(f"\nMensaje: {mensaje}")
    print(f"\nResultado:\n{json.dumps(resultado, indent=2)}")

    assert resultado["total_enviado"] == 600
    assert resultado["reserva"] == 70, f"Reserva debería ser 70, obtuvo {resultado['reserva']}"
    assert resultado["valido"] == True, "Debería ser válido"
    assert resultado["alerta"] is None, "No debería tener alerta"

    print("\n✓ TEST PASÓ")
    return True

def test_sin_total():
    """
    Caso: Usuario solo dice "380 renta, 120 comida, 30 Estefanito"
    Sin total_enviado, el total = suma desglose = 530
    """
    print("\n" + "="*70)
    print("TEST 4: Sin total_enviado - 380 renta, 120 comida, 30 Estefanito")
    print("="*70)

    mensaje = "380 renta, 120 comida, 30 Estefanito"
    resultado = extraer_gastos(mensaje)

    print(f"\nMensaje: {mensaje}")
    print(f"\nResultado:\n{json.dumps(resultado, indent=2)}")

    # Sin total_enviado explícito, se asume que el total es la suma del desglose
    assert resultado["total_enviado"] == 530, f"Total debería ser 530 (suma desglose)"
    assert resultado["reserva"] == 0, "Reserva debería ser 0"
    assert resultado["valido"] == True, "Debería ser válido"

    print("\n✓ TEST PASÓ")
    return True

def test_solo_una_categoria():
    """
    Caso: Usuario dice "Envié 100 para renta"
    """
    print("\n" + "="*70)
    print("TEST 5: Una categoría - Envié 100 para renta")
    print("="*70)

    mensaje = "Envié 100 para renta"
    resultado = extraer_gastos(mensaje)

    print(f"\nMensaje: {mensaje}")
    print(f"\nResultado:\n{json.dumps(resultado, indent=2)}")

    assert resultado["total_enviado"] == 100
    assert resultado["desglose"]["renta"] == 100
    assert resultado["reserva"] == 0, "Reserva debería ser 0"
    assert resultado["valido"] == True, "Debería ser válido"

    print("\n✓ TEST PASÓ")
    return True

def run_all_tests():
    """Ejecuta todos los tests"""
    print("\n" + "="*70)
    print("INICIANDO SUITE DE TESTS - extraer_gastos()")
    print("="*70)

    tests = [
        test_caso_usuario,
        test_sin_pasarse,
        test_con_reserva,
        test_sin_total,
        test_solo_una_categoria,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"\n✗ TEST FALLÓ: {str(e)}")
            failed += 1
        except Exception as e:
            print(f"\n✗ ERROR EN TEST: {str(e)}")
            failed += 1

    print("\n" + "="*70)
    print(f"RESULTADOS: {passed} pasaron, {failed} fallaron")
    print("="*70 + "\n")

    return failed == 0

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
