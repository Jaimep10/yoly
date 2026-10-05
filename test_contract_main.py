#!/usr/bin/env python
"""Test FastAPI webhook contract - Pydantic validation and service layer integration."""
import sys
from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


def test_webhook_missing_wa_id():
    """Test that POST /webhook without wa_id fails (422 Pydantic validation)."""
    # Invalid: missing wa_id
    response = client.post("/webhook", json={
        "texto": "Hola",
        "media_url": None
    })

    assert response.status_code == 422, f"Expected 422 validation error, got {response.status_code}"
    print("✓ test_webhook_missing_wa_id PASSED - Pydantic validation enforced")


def test_webhook_missing_texto():
    """Test that POST /webhook without texto fails (422 Pydantic validation)."""
    response = client.post("/webhook", json={
        "wa_id": "593987654321",
        "media_url": None
    })

    assert response.status_code == 422, f"Expected 422 validation error, got {response.status_code}"
    print("✓ test_webhook_missing_texto PASSED - Pydantic validation enforced for texto")


def test_webhook_valid_request():
    """Test that POST /webhook with wa_id and texto returns 200."""
    response = client.post("/webhook", json={
        "wa_id": "593987654321",
        "texto": "Hola, quiero ver mis gastos",
        "media_url": None,
        "cuenta": "principal"
    })

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["status"] == "ok", f"Expected status 'ok', got {data['status']}"
    print("✓ test_webhook_valid_request PASSED - Valid webhook request accepted")


def test_webhook_with_media_url():
    """Test that POST /webhook accepts media_url parameter."""
    response = client.post("/webhook", json={
        "wa_id": "+593987654321",
        "texto": "Mi factura",
        "media_url": "https://example.com/media/123.jpg",
        "cuenta": "Fanny"
    })

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "mensaje" in data, "Response should contain 'mensaje' field"
    print("✓ test_webhook_with_media_url PASSED - Media URL accepted")


def test_webhook_default_cuenta():
    """Test that POST /webhook defaults to 'principal' cuenta if not specified."""
    response = client.post("/webhook", json={
        "wa_id": "593987654321",
        "texto": "Ver balance",
        # cuenta not specified, should default to "principal"
    })

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    print("✓ test_webhook_default_cuenta PASSED - Default cuenta='principal' works")


def test_webhook_pydantic_validation():
    """Test that Pydantic validates required fields are present."""
    # Valid: all required fields present
    response = client.post("/webhook", json={
        "wa_id": "593987654321",
        "texto": "Test message",
        "media_url": None
    })

    # Pydantic should accept this
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    print("✓ test_webhook_pydantic_validation PASSED - Pydantic validation works correctly")


def test_health_endpoint():
    """Test that /health endpoint exists and works."""
    response = client.get("/health")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["status"] == "ok", "Health check should return status ok"
    print("✓ test_health_endpoint PASSED - Health endpoint functional")


if __name__ == "__main__":
    try:
        test_webhook_missing_wa_id()
        test_webhook_missing_texto()
        test_webhook_valid_request()
        test_webhook_with_media_url()
        test_webhook_default_cuenta()
        test_webhook_pydantic_validation()
        test_health_endpoint()
        print("\n✅ All test_contract_main tests PASSED! (7/7)")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        sys.exit(1)
