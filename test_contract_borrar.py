#!/usr/bin/env python
"""Test delete/remove gasto functionality in Guía agent."""
import sys
from agents.guide import Guía


def test_parse_comando_borrar_ultima():
    """Test deleting the last gasto with 'borrar ultima'."""
    guia = Guía()

    gastos = [
        {"id": "g1", "cliente": "Tienda A", "monto": 1000},
        {"id": "g2", "cliente": "Tienda B", "monto": 1000},
    ]

    resultado = guia.parse_comando_borrar("593987654321", "borrar ultima", gastos)

    assert resultado["detectado"] == True, "Debe detectar comando"
    assert resultado["criterio"] == "ultima", f"Criterio debe ser 'ultima', obtenido: {resultado['criterio']}"
    assert resultado["gasto_encontrado"] is not None, "Debe encontrar el último gasto"
    assert resultado["gasto_encontrado"]["id"] == "g2", "Debe ser el último gasto (g2)"
    assert resultado["mensaje"] is not None, "Debe retornar mensaje"
    assert "Tienda B" in resultado["mensaje"], f"Mensaje debe contener cliente: {resultado['mensaje']}"
    assert "$1,000" in resultado["mensaje"] or "$1000" in resultado["mensaje"], f"Mensaje debe contener monto: {resultado['mensaje']}"

    print("✓ test_parse_comando_borrar_ultima PASSED - Borrar última funciona")


def test_parse_comando_borrar_cliente():
    """Test deleting gasto by client name."""
    guia = Guía()

    gastos = [
        {"id": "g1", "cliente": "Cristina", "monto": 500},
        {"id": "g2", "cliente": "Juan", "monto": 1000},
    ]

    resultado = guia.parse_comando_borrar("593987654321", "borrar Cristina", gastos)

    assert resultado["detectado"] == True
    assert resultado["criterio"] == "cliente", f"Criterio debe ser 'cliente', obtenido: {resultado['criterio']}"
    assert resultado["gasto_encontrado"] is not None
    assert resultado["gasto_encontrado"]["id"] == "g1", "Debe encontrar gasto de Cristina"
    assert "Cristina" in resultado["mensaje"]
    assert "$500" in resultado["mensaje"] or "500" in resultado["mensaje"]

    print("✓ test_parse_comando_borrar_cliente PASSED - Borrar por cliente funciona")


def test_parse_comando_borrar_monto():
    """Test deleting gasto by amount."""
    guia = Guía()

    gastos = [
        {"id": "g1", "cliente": "Tienda A", "monto": 3000},
        {"id": "g2", "cliente": "Tienda B", "monto": 500},
    ]

    resultado = guia.parse_comando_borrar("593987654321", "borrar $3000", gastos)

    assert resultado["detectado"] == True
    assert resultado["criterio"] == "monto", f"Criterio debe ser 'monto', obtenido: {resultado['criterio']}"
    assert resultado["gasto_encontrado"] is not None
    assert resultado["gasto_encontrado"]["id"] == "g1", "Debe encontrar gasto de $3000"
    assert "Tienda A" in resultado["mensaje"]

    print("✓ test_parse_comando_borrar_monto PASSED - Borrar por monto funciona")


def test_parse_comando_borrar_no_detecta_pregunta():
    """Test that normal questions are not detected as delete commands."""
    guia = Guía()

    resultado = guia.parse_comando_borrar("593987654321", "ver mis gastos", [])

    assert resultado["detectado"] == False, "No debe detectar 'ver gastos' como comando borrar"
    assert resultado["criterio"] is None
    assert resultado["gasto_encontrado"] is None
    assert resultado["mensaje"] is None

    print("✓ test_parse_comando_borrar_no_detecta_pregunta PASSED - No confunde con preguntas")


def test_parse_comando_borrar_sin_coincidencia():
    """Test delete command when no matching gasto exists."""
    guia = Guía()

    gastos = [
        {"id": "g1", "cliente": "Tienda A", "monto": 1000},
    ]

    # Buscar cliente que no existe
    resultado = guia.parse_comando_borrar("593987654321", "borrar Roberto", gastos)

    assert resultado["detectado"] == True, "Debe detectar comando"
    assert resultado["gasto_encontrado"] is None, "No debe encontrar gasto"
    assert "No encontré" in resultado["mensaje"], f"Mensaje debe indicar que no encontró: {resultado['mensaje']}"

    print("✓ test_parse_comando_borrar_sin_coincidencia PASSED - Maneja sin coincidencias")


def test_parse_comando_borrar_gastos_vacios():
    """Test delete command with empty gastos list."""
    guia = Guía()

    resultado = guia.parse_comando_borrar("593987654321", "borrar ultima", [])

    assert resultado["detectado"] == True
    assert resultado["criterio"] == "ultima"
    assert resultado["gasto_encontrado"] is None, "No debe encontrar gasto en lista vacía"
    assert "No hay gastos" in resultado["mensaje"], f"Mensaje debe indicar sin gastos: {resultado['mensaje']}"

    print("✓ test_parse_comando_borrar_gastos_vacios PASSED - Maneja lista vacía")


def test_parse_comando_borrar_variaciones():
    """Test different ways to say 'delete'."""
    guia = Guía()

    gastos = [
        {"id": "g1", "cliente": "Test", "monto": 100},
    ]

    # Test "eliminar"
    resultado = guia.parse_comando_borrar("593987654321", "eliminar ultima", gastos)
    assert resultado["detectado"] == True

    # Test "anular"
    resultado = guia.parse_comando_borrar("593987654321", "anular ultima", gastos)
    assert resultado["detectado"] == True

    # Test "delete" en inglés
    resultado = guia.parse_comando_borrar("593987654321", "delete last", gastos)
    assert resultado["detectado"] == True

    print("✓ test_parse_comando_borrar_variaciones PASSED - Detecta variaciones")


if __name__ == "__main__":
    try:
        test_parse_comando_borrar_ultima()
        test_parse_comando_borrar_cliente()
        test_parse_comando_borrar_monto()
        test_parse_comando_borrar_no_detecta_pregunta()
        test_parse_comando_borrar_sin_coincidencia()
        test_parse_comando_borrar_gastos_vacios()
        test_parse_comando_borrar_variaciones()
        print("\n✅ All test_contract_borrar tests PASSED! (7/7)")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        sys.exit(1)
