#!/usr/bin/env python
"""Test multi-account support in Yoly."""
import os
import json
import tempfile
import shutil
from pathlib import Path

# Temporarily override DATA_DIR for testing
import main
original_data_dir = main.DATA_DIR
test_data_dir = tempfile.mkdtemp(prefix="yoly_test_")
main.DATA_DIR = test_data_dir

def test_obtener_ruta_datos():
    """Test that obtener_ruta_datos creates account subdirectories."""
    phone = "1234567890"

    # Test default account "principal"
    ruta_principal = main.obtener_ruta_datos(phone)
    assert "principal" in ruta_principal, f"Path should contain 'principal': {ruta_principal}"
    assert os.path.exists(ruta_principal), f"Principal account directory should exist: {ruta_principal}"

    # Test other accounts
    ruta_negocio = main.obtener_ruta_datos(phone, "negocio")
    assert "negocio" in ruta_negocio, f"Path should contain 'negocio': {ruta_negocio}"
    assert os.path.exists(ruta_negocio), f"Negocio account directory should exist: {ruta_negocio}"

    # Test personal account
    ruta_personal = main.obtener_ruta_datos(phone, "personal")
    assert "personal" in ruta_personal, f"Path should contain 'personal': {ruta_personal}"
    assert os.path.exists(ruta_personal), f"Personal account directory should exist: {ruta_personal}"

    print("✓ obtener_ruta_datos test passed")

def test_cargar_guardar_gastos():
    """Test that expenses are saved per account."""
    phone = "9876543210"

    # Save expenses for principal account
    gastos_principal = [
        {"id": "gasto1", "monto": 100, "descripcion": "Comida", "fecha": "2026-10-01"},
        {"id": "gasto2", "monto": 50, "descripcion": "Transporte", "fecha": "2026-10-02"}
    ]
    main.guardar_gastos(phone, gastos_principal, "principal")

    # Save different expenses for negocio account
    gastos_negocio = [
        {"id": "gasto3", "monto": 500, "descripcion": "Inventario", "fecha": "2026-10-01"},
        {"id": "gasto4", "monto": 200, "descripcion": "Alquiler", "fecha": "2026-10-02"}
    ]
    main.guardar_gastos(phone, gastos_negocio, "negocio")

    # Load and verify
    loaded_principal = main.cargar_gastos(phone, "principal")
    assert len(loaded_principal) == 2, f"Principal should have 2 gastos, got {len(loaded_principal)}"
    assert loaded_principal[0]["id"] == "gasto1"

    loaded_negocio = main.cargar_gastos(phone, "negocio")
    assert len(loaded_negocio) == 2, f"Negocio should have 2 gastos, got {len(loaded_negocio)}"
    assert loaded_negocio[0]["id"] == "gasto3"

    # Personal should be empty
    loaded_personal = main.cargar_gastos(phone, "personal")
    assert len(loaded_personal) == 0, f"Personal should be empty, got {len(loaded_personal)}"

    print("✓ cargar_guardar_gastos test passed")

def test_cargar_guardar_memoria():
    """Test that memory is saved per account."""
    phone = "5555555555"

    # Save memory for principal
    memoria_principal = {"phone": phone, "ultimo_pago": "2026-10-05", "cuenta": "principal"}
    main.guardar_memoria(memoria_principal, phone, "principal")

    # Save memory for negocio
    memoria_negocio = {"phone": phone, "ultima_compra": "2026-10-04", "cuenta": "negocio"}
    main.guardar_memoria(memoria_negocio, phone, "negocio")

    # Load and verify
    loaded_principal = main.cargar_memoria(phone, "principal")
    assert loaded_principal.get("cuenta") == "principal"
    assert loaded_principal.get("ultimo_pago") == "2026-10-05"

    loaded_negocio = main.cargar_memoria(phone, "negocio")
    assert loaded_negocio.get("cuenta") == "negocio"
    assert loaded_negocio.get("ultima_compra") == "2026-10-04"

    print("✓ cargar_guardar_memoria test passed")

def test_contexto_agentes():
    """Test that Contexto includes cuenta parameter."""
    phone = "1111111111"

    # Create context for principal
    ctx_principal = main.contexto_agentes(phone, "principal")
    assert ctx_principal.cuenta == "principal"
    assert ctx_principal.phone_clean == "1111111111"

    # Create context for negocio
    ctx_negocio = main.contexto_agentes(phone, "negocio")
    assert ctx_negocio.cuenta == "negocio"
    assert ctx_negocio.phone_clean == "1111111111"

    print("✓ contexto_agentes test passed")

def cleanup():
    """Clean up test data."""
    if os.path.exists(test_data_dir):
        shutil.rmtree(test_data_dir)
    main.DATA_DIR = original_data_dir
    print("✓ Cleanup complete")

if __name__ == "__main__":
    try:
        test_obtener_ruta_datos()
        test_cargar_guardar_gastos()
        test_cargar_guardar_memoria()
        test_contexto_agentes()
        print("\n✅ All multi-account tests passed!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        exit(1)
    finally:
        cleanup()
