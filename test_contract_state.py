#!/usr/bin/env python
"""Test state isolation by wa_id and cuenta - prevent data mixing."""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

# Override DATA_DIR for testing
import app.core.state as state_module
original_data_dir = state_module.DATA_DIR
test_data_dir = tempfile.mkdtemp(prefix="yoly_test_state_")
state_module.DATA_DIR = test_data_dir


def cleanup():
    """Clean up test data."""
    if os.path.exists(test_data_dir):
        shutil.rmtree(test_data_dir)
    print("✓ Cleanup complete")


def test_wa_id_isolation():
    """
    CRITICAL: Verify that wa_id A does NOT see data from wa_id B.

    Scenario:
    - wa_id A (+1 516 386 9020) with balance 2000
    - wa_id B (+593 987 654 321) with balance 500

    Expected: Each wa_id should only see its own data when using get_estado.
    """
    wa_id_a = "15163869020"
    wa_id_b = "593987654321"
    cuenta = "principal"

    # Set state for wa_id A
    estado_a = state_module.get_estado(wa_id_a, cuenta)
    gastos_a = [
        {"id": "a1", "monto": 1000, "cliente": "Tienda A", "fecha": "2026-10-01"},
        {"id": "a2", "monto": 1000, "cliente": "Tienda B", "fecha": "2026-10-02"}
    ]
    state_module.guardar_gastos(wa_id_a, gastos_a, cuenta)

    # Set state for wa_id B
    estado_b = state_module.get_estado(wa_id_b, cuenta)
    gastos_b = [
        {"id": "b1", "monto": 250, "cliente": "Tienda C", "fecha": "2026-10-01"},
        {"id": "b2", "monto": 250, "cliente": "Tienda D", "fecha": "2026-10-02"}
    ]
    state_module.guardar_gastos(wa_id_b, gastos_b, cuenta)

    # Load and verify isolation
    loaded_a = state_module.cargar_gastos(wa_id_a, cuenta)
    loaded_b = state_module.cargar_gastos(wa_id_b, cuenta)

    # ASSERTION: A should only see A's data (2000 total)
    total_a = sum(g["monto"] for g in loaded_a)
    assert total_a == 2000, f"Phone A should have 2000, got {total_a}"
    assert len(loaded_a) == 2, f"Phone A should have 2 gastos, got {len(loaded_a)}"
    assert all(g["id"].startswith("a") for g in loaded_a), "Phone A should only see 'a' gastos"

    # ASSERTION: B should only see B's data (500 total)
    total_b = sum(g["monto"] for g in loaded_b)
    assert total_b == 500, f"Phone B should have 500, got {total_b}"
    assert len(loaded_b) == 2, f"Phone B should have 2 gastos, got {len(loaded_b)}"
    assert all(g["id"].startswith("b") for g in loaded_b), "Phone B should only see 'b' gastos"

    # CRITICAL: Verify no cross-contamination
    assert total_a != total_b, "Totals should be different"
    a_ids = {g["id"] for g in loaded_a}
    b_ids = {g["id"] for g in loaded_b}
    assert a_ids.isdisjoint(b_ids), f"A and B should not share any gasto IDs. A={a_ids}, B={b_ids}"

    print("✓ test_wa_id_isolation PASSED - No cross-contamination detected")


def test_cuenta_isolation():
    """
    Verify that set_estado isolates data by wa_id AND cuenta.

    Scenario:
    - Phone +1516 with cuenta "Fanny" has estado X
    - Phone +593 with cuenta "Fanny" has estado Y

    Expected: Each (phone, cuenta) pair has independent state.
    """
    wa_id_1 = "+1516"
    wa_id_2 = "+593"
    cuenta_fanny = "Fanny"

    # Set different estado for same cuenta but different phones
    state_module.set_estado(wa_id_1, cuenta_fanny, "esperando_menu", esperando_menu=True)
    state_module.set_estado(wa_id_2, cuenta_fanny, "esperando_menu", esperando_menu=True)

    # Load and verify they're independent
    estado_1 = state_module.get_estado(wa_id_1, cuenta_fanny)
    estado_2 = state_module.get_estado(wa_id_2, cuenta_fanny)

    # Both should have the estado, but separate files
    assert estado_1["wa_id"] == wa_id_1, f"Estado 1 should have wa_id {wa_id_1}"
    assert estado_2["wa_id"] == wa_id_2, f"Estado 2 should have wa_id {wa_id_2}"

    # Verify separate memory files exist
    ruta_1 = state_module.obtener_ruta_datos(wa_id_1, cuenta_fanny)
    ruta_2 = state_module.obtener_ruta_datos(wa_id_2, cuenta_fanny)

    assert ruta_1 != ruta_2, f"Paths should be different: {ruta_1} vs {ruta_2}"

    print("✓ test_cuenta_isolation PASSED - Different (wa_id, cuenta) pairs are isolated")


def test_multiple_cuentas_same_wa_id():
    """
    Verify that one wa_id can have multiple independent cuentas.

    Scenario:
    - wa_id "593123456789" has cuenta "Fanny" with 1000 pesos
    - wa_id "593123456789" has cuenta "Negocio" with 5000 pesos

    Expected: Each cuenta for the same wa_id is independent.
    """
    wa_id = "593123456789"
    cuenta_fanny = "Fanny"
    cuenta_negocio = "Negocio"

    # Add gastos to Fanny cuenta
    gastos_fanny = [
        {"id": "f1", "monto": 500, "cliente": "Personal", "fecha": "2026-10-01"},
        {"id": "f2", "monto": 500, "cliente": "Personal", "fecha": "2026-10-02"}
    ]
    state_module.guardar_gastos(wa_id, gastos_fanny, cuenta_fanny)

    # Add different gastos to Negocio cuenta
    gastos_negocio = [
        {"id": "n1", "monto": 2000, "cliente": "Proveedor", "fecha": "2026-10-01"},
        {"id": "n2", "monto": 3000, "cliente": "Inventario", "fecha": "2026-10-02"}
    ]
    state_module.guardar_gastos(wa_id, gastos_negocio, cuenta_negocio)

    # Verify isolation between cuentas
    loaded_fanny = state_module.cargar_gastos(wa_id, cuenta_fanny)
    loaded_negocio = state_module.cargar_gastos(wa_id, cuenta_negocio)

    total_fanny = sum(g["monto"] for g in loaded_fanny)
    total_negocio = sum(g["monto"] for g in loaded_negocio)

    assert total_fanny == 1000, f"Fanny should have 1000, got {total_fanny}"
    assert total_negocio == 5000, f"Negocio should have 5000, got {total_negocio}"
    assert len(loaded_fanny) == 2, f"Fanny should have 2 gastos"
    assert len(loaded_negocio) == 2, f"Negocio should have 2 gastos"

    # Verify no ID overlap
    fanny_ids = {g["id"] for g in loaded_fanny}
    negocio_ids = {g["id"] for g in loaded_negocio}
    assert fanny_ids.isdisjoint(negocio_ids), "Accounts should not share gasto IDs"

    print("✓ test_multiple_cuentas_same_wa_id PASSED - Multiple cuentas per wa_id are isolated")


def test_estado_memory_isolation():
    """
    Verify that memoria (memory) is also isolated by wa_id and cuenta.
    """
    wa_id_a = "1234567890"
    wa_id_b = "9876543210"
    cuenta = "principal"

    # Save different memoria for each wa_id
    memoria_a = {"key": "value_a", "data": {"user": "A"}}
    memoria_b = {"key": "value_b", "data": {"user": "B"}}

    state_module.guardar_memoria(memoria_a, wa_id_a, cuenta)
    state_module.guardar_memoria(memoria_b, wa_id_b, cuenta)

    # Load and verify isolation
    loaded_a = state_module.cargar_memoria(wa_id_a, cuenta)
    loaded_b = state_module.cargar_memoria(wa_id_b, cuenta)

    assert loaded_a != loaded_b, "Memory should be different for different wa_ids"
    assert loaded_a.get("key") == "value_a", "Memory A should have value_a"
    assert loaded_b.get("key") == "value_b", "Memory B should have value_b"

    print("✓ test_estado_memory_isolation PASSED - Memory is properly isolated")


if __name__ == "__main__":
    try:
        test_wa_id_isolation()
        test_cuenta_isolation()
        test_multiple_cuentas_same_wa_id()
        test_estado_memory_isolation()
        print("\n✅ All test_contract_state tests PASSED! (4/4)")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        exit(1)
    finally:
        cleanup()
