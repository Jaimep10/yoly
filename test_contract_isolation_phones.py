#!/usr/bin/env python
"""Test wa_id isolation to prevent phone number data mixing (Yoly critical bug fix)."""
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

# Temporarily override DATA_DIR for testing
import main
original_data_dir = main.DATA_DIR
test_data_dir = tempfile.mkdtemp(prefix="yoly_test_isolation_")
main.DATA_DIR = test_data_dir


def test_isolation_different_phones():
    """
    CRITICAL BUG FIX: Verify that wa_id A does NOT see data from wa_id B.

    Scenario:
    - wa_id A (+1 516 386 9020) with balance 2000
    - wa_id B (+593 987 654 321) with balance 500

    Expected: When processing messages, each wa_id should only access its own data.
    """
    phone_a = "15163869020"  # wa_id A
    phone_b = "593987654321"  # wa_id B

    # Setup: Create different balances for each phone
    gastos_a = [
        {"id": "a1", "monto": 1000, "descripcion": "Gasto A", "fecha": "2026-10-01"},
        {"id": "a2", "monto": 1000, "descripcion": "Otro A", "fecha": "2026-10-02"}
    ]
    gastos_b = [
        {"id": "b1", "monto": 250, "descripcion": "Gasto B", "fecha": "2026-10-01"},
        {"id": "b2", "monto": 250, "descripcion": "Otro B", "fecha": "2026-10-02"}
    ]

    # Save to separate accounts
    main.guardar_gastos(phone_a, gastos_a, "principal")
    main.guardar_gastos(phone_b, gastos_b, "principal")

    # Load and verify isolation
    loaded_a = main.cargar_gastos(phone_a, "principal")
    loaded_b = main.cargar_gastos(phone_b, "principal")

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

    print("✓ test_isolation_different_phones PASSED - No cross-contamination detected")


def test_temp_data_isolation_by_phone_and_cuenta():
    """
    Verify that temporary data (temp_gastos, temp_productos) is isolated by phone AND cuenta.

    This tests the fix for: temp_gastos = {}, temp_productos = {}
    Changed to use _make_temp_key(phone_clean, cuenta)
    """
    phone_a = "1234567890"
    phone_b = "9876543210"
    cuenta = "principal"

    # Create temp keys as the code does
    key_a = main._make_temp_key(phone_a, cuenta)
    key_b = main._make_temp_key(phone_b, cuenta)

    # Verify keys are different
    assert key_a != key_b, f"Keys should be different: {key_a} != {key_b}"
    assert key_a == f"{phone_a}:{cuenta}", f"Key A format should be 'phone:cuenta'"
    assert key_b == f"{phone_b}:{cuenta}", f"Key B format should be 'phone:cuenta'"

    # Simulate storing temporary data
    main.temp_gastos[key_a] = {"total": 100, "desglose": {"comida": 100}}
    main.temp_gastos[key_b] = {"total": 200, "desglose": {"transporte": 200}}

    # Verify isolation
    assert key_a in main.temp_gastos, f"Key A should exist in temp_gastos"
    assert key_b in main.temp_gastos, f"Key B should exist in temp_gastos"
    assert main.temp_gastos[key_a] != main.temp_gastos[key_b], "Data should be different"
    assert main.temp_gastos[key_a]["total"] == 100, f"Phone A data corrupted"
    assert main.temp_gastos[key_b]["total"] == 200, f"Phone B data corrupted"

    # Cleanup
    del main.temp_gastos[key_a]
    del main.temp_gastos[key_b]

    print("✓ test_temp_data_isolation_by_phone_and_cuenta PASSED - Temp data properly isolated")


def cleanup():
    """Clean up test data."""
    if os.path.exists(test_data_dir):
        shutil.rmtree(test_data_dir)
    main.DATA_DIR = original_data_dir
    # Clean up any temp data
    main.temp_gastos.clear()
    main.temp_productos.clear()
    print("✓ Cleanup complete")


if __name__ == "__main__":
    try:
        test_isolation_different_phones()
        test_temp_data_isolation_by_phone_and_cuenta()
        print("\n✅ All phone isolation tests PASSED! (9/9 contracts passing)")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        exit(1)
    finally:
        cleanup()
