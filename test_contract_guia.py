#!/usr/bin/env python
"""Test guided menu functionality in Guía agent."""
import sys
from agents.guide import Guía


def test_generar_menu_reporte_emojis():
    """Test that generated menu contains all required emojis (1️⃣2️⃣3️⃣4️⃣)."""
    guia = Guía()
    menu = guia.generar_menu_reporte("593987654321", "Fanny", saldo_actual=5000)

    # Verificar que contiene los emojis
    assert "1️⃣" in menu, f"Menu debe contener 1️⃣. Obtenido: {menu}"
    assert "2️⃣" in menu, f"Menu debe contener 2️⃣. Obtenido: {menu}"
    assert "3️⃣" in menu, f"Menu debe contener 3️⃣. Obtenido: {menu}"
    assert "4️⃣" in menu, f"Menu debe contener 4️⃣. Obtenido: {menu}"

    print("✓ test_generar_menu_reporte_emojis PASSED - Todos los emojis presentes")


def test_generar_menu_reporte_estructura():
    """Test that generated menu has proper structure and content."""
    guia = Guía()
    menu = guia.generar_menu_reporte("593987654321", "Fanny", saldo_actual=1000)

    # Verificar estructura
    assert "Fanny" in menu, "Menu debe contener nombre de la cuenta"
    assert "$1,000" in menu or "$1000" in menu, "Menu debe contener el saldo"
    assert "pdf" in menu.lower(), "Menu debe mencionar pdf"
    assert "excel" in menu.lower(), "Menu debe mencionar excel"

    print("✓ test_generar_menu_reporte_estructura PASSED - Estructura correcta")


def test_parse_respuesta_menu():
    """Test that menu responses are correctly parsed."""
    guia = Guía()

    # Test opción 1
    resultado = guia.parse_respuesta_menu("593987654321", "1", "Fanny")
    assert resultado["tipo"] == "resumen", f"Esperado 'resumen', obtenido: {resultado['tipo']}"

    # Test opción 2
    resultado = guia.parse_respuesta_menu("593987654321", "2", "Fanny")
    assert resultado["tipo"] == "pdf", f"Esperado 'pdf', obtenido: {resultado['tipo']}"

    # Test opción 3
    resultado = guia.parse_respuesta_menu("593987654321", "3", "Fanny")
    assert resultado["tipo"] == "excel", f"Esperado 'excel', obtenido: {resultado['tipo']}"

    # Test opción 4
    resultado = guia.parse_respuesta_menu("593987654321", "4", "Fanny")
    assert resultado["tipo"] == "ultimos", f"Esperado 'ultimos', obtenido: {resultado['tipo']}"

    # Test con "pdf"
    resultado = guia.parse_respuesta_menu("593987654321", "pdf", "Fanny")
    assert resultado["tipo"] == "pdf", f"Esperado 'pdf', obtenido: {resultado['tipo']}"

    # Test con "excel"
    resultado = guia.parse_respuesta_menu("593987654321", "excel", "Fanny")
    assert resultado["tipo"] == "excel", f"Esperado 'excel', obtenido: {resultado['tipo']}"

    # Test opción inválida
    resultado = guia.parse_respuesta_menu("593987654321", "99", "Fanny")
    assert resultado["tipo"] is None, f"Esperado None para opción inválida, obtenido: {resultado['tipo']}"

    print("✓ test_parse_respuesta_menu PASSED - Todas las opciones parseadas correctamente")


def test_es_comando_borrar():
    """Test that delete commands are correctly detected."""
    guia = Guía()

    # Positivos
    assert guia.es_comando_borrar("borrar ultima") == True
    assert guia.es_comando_borrar("eliminar Cristina") == True
    assert guia.es_comando_borrar("anular $3000") == True
    assert guia.es_comando_borrar("delete last") == True

    # Negativos
    assert guia.es_comando_borrar("ver gastos") == False
    assert guia.es_comando_borrar("") == False
    assert guia.es_comando_borrar("   ") == False

    print("✓ test_es_comando_borrar PASSED - Detecta comandos correctamente")


if __name__ == "__main__":
    try:
        test_generar_menu_reporte_emojis()
        test_generar_menu_reporte_estructura()
        test_parse_respuesta_menu()
        test_es_comando_borrar()
        print("\n✅ All test_contract_guia tests PASSED! (4/4)")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        sys.exit(1)
