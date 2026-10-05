#!/usr/bin/env python
"""Test that menu does not mention logo/pro but reports internally include logo."""
import sys
from agents.guide import Guía
from app.services import reporte_service


def test_menu_no_logo_mention():
    """Test that generated menu does NOT contain 'logo' or 'pro' words."""
    guia = Guía()
    menu = guia.generar_menu_reporte("593987654321", "Fanny", saldo_actual=5000)

    # Assert: "logo" NOT in menu
    assert "logo" not in menu.lower(), f"Menu debe NOT contener 'logo'. Obtenido: {menu}"

    # Assert: "pro" NOT in menu
    assert "pro" not in menu.lower(), f"Menu debe NOT contener 'pro'. Obtenido: {menu}"

    print("✓ test_menu_no_logo_mention PASSED - No mentions of 'logo' or 'pro'")


def test_menu_simplified_text():
    """Test that generated menu has simplified text."""
    guia = Guía()
    menu = guia.generar_menu_reporte("593987654321", "Fanny", saldo_actual=5000)

    # Assert: "1️⃣ Ver en web" in menu
    assert "1️⃣" in menu and "Ver en web" in menu, f"Menu debe contener '1️⃣ Ver en web'. Obtenido: {menu}"

    # Assert: "2️⃣ PDF" in menu (without "pro")
    assert "2️⃣" in menu and "PDF" in menu, f"Menu debe contener '2️⃣ PDF'. Obtenido: {menu}"

    # Assert: "3️⃣ Excel" in menu (without "pro")
    assert "3️⃣" in menu and "Excel" in menu, f"Menu debe contener '3️⃣ Excel'. Obtenido: {menu}"

    # Assert: "4️⃣ Solo guardar" in menu
    assert "4️⃣" in menu and "Solo guardar" in menu, f"Menu debe contener '4️⃣ Solo guardar'. Obtenido: {menu}"

    print("✓ test_menu_simplified_text PASSED - Menu has simplified text without pro/logo")


def test_menu_question_text():
    """Test that menu asks 'Quieres el reporte?' instead of old text."""
    guia = Guía()
    menu = guia.generar_menu_reporte("593987654321", "Fanny", saldo_actual=5000)

    # Assert: "Quieres el reporte?" in menu
    assert "Quieres el reporte?" in menu, f"Menu debe contener 'Quieres el reporte?'. Obtenido: {menu}"

    print("✓ test_menu_question_text PASSED - Menu asks for report correctly")


if __name__ == "__main__":
    try:
        test_menu_no_logo_mention()
        test_menu_simplified_text()
        test_menu_question_text()
        print("\n✅ All test_contract_menu_logo tests PASSED! (3/3)")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        sys.exit(1)
