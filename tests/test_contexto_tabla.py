# Después de "¿Te mando tabla al dashboard?", el "sí" del usuario debe traer el link,
# no caer a Claude con un "¿qué necesitas?".
from unittest.mock import MagicMock

import pytest

import main

TEL = "whatsapp:+593991234567"
PHONE = main.normalizar_telefono(TEL)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "guardar_memoria", lambda m: None)
    monkeypatch.setattr(main, "cargar_memoria", lambda: {})
    monkeypatch.setattr(main, "memoria_usuarios", {})
    monkeypatch.setattr(main, "temp_gastos", {})
    monkeypatch.setattr(main, "temp_productos", {})
    claude = MagicMock()
    claude.messages.create.return_value.content = [MagicMock(text="¿Qué necesitas?")]
    monkeypatch.setattr(main, "client", claude)
    return claude


def pregunta_tabla_pendiente():
    main.guardar_cobro(PHONE, {
        "tipo": "cobro_deuda", "cliente": "Alan", "deuda": 1000, "pagado": 300, "saldo": 700,
        "pagos": [{"fecha": "2026-10-01", "monto": 100}, {"fecha": "2026-10-02", "monto": 200}],
        "ultima_pregunta": "dashboard",
    })


def enviar(texto):
    salida = main.Salida()
    main.procesar_mensaje(texto, TEL, "https://yoly.test", salida)
    return "\n".join(salida.textos)


@pytest.mark.parametrize("texto", ["si", "Sí", "Si.", "si porfa", "Sí, mándala", "dale",
                                   "ok 👍", "si quiero la tabla", "claro que si"])
def test_si_a_la_tabla_manda_link(entorno, texto):
    pregunta_tabla_pendiente()
    respuesta = enviar(texto)
    assert f"https://yoly.test/dashboard/{PHONE}" in respuesta
    entorno.messages.create.assert_not_called()
    assert main.obtener_ultima_pregunta(PHONE) == ""


def test_si_despues_de_reinicio_con_datos_manda_link(entorno):
    pregunta_tabla_pendiente()
    main.memoria_usuarios[PHONE]["ultima_pregunta"] = ""  # memoria perdida
    assert "/dashboard/" in enviar("si")


def test_no_a_la_tabla_no_manda_link(entorno):
    pregunta_tabla_pendiente()
    respuesta = enviar("no")
    assert "/dashboard/" not in respuesta
    entorno.messages.create.assert_not_called()
    assert main.obtener_ultima_pregunta(PHONE) == ""


def test_si_con_gasto_pendiente_confirma_el_gasto(entorno):
    pregunta_tabla_pendiente()
    main.temp_gastos[TEL] = {"total_enviado": 500, "desglose": {"renta": 380, "comida": 120},
                             "reserva": 0, "diferencia": 0}
    respuesta = enviar("si")
    assert "Listo" in respuesta and "Renta" in respuesta
    assert TEL not in main.temp_gastos


def test_balance_sigue_funcionando(entorno):
    pregunta_tabla_pendiente()
    assert "Te falta: $700" in enviar("cuanto debo")


def test_pedir_tabla_sin_pregunta(entorno):
    assert "/dashboard/" in enviar("mándame la tabla")


@pytest.mark.parametrize("texto", ["1", "1.", "1️⃣", " 1 ", "uno"])
def test_opcion_1_manda_link_aunque_se_pierda_la_memoria(entorno, texto):
    # Sin pregunta guardada ni datos (como después de un reinicio de Render)
    assert f"https://yoly.test/dashboard/{PHONE}" in enviar(texto)
    entorno.messages.create.assert_not_called()


@pytest.mark.parametrize("texto", ["2", "2️⃣", "dos"])
def test_opcion_2_no_manda_link(entorno, texto):
    pregunta_tabla_pendiente()
    respuesta = enviar(texto)
    assert "/dashboard/" not in respuesta
    entorno.messages.create.assert_not_called()
    assert main.obtener_ultima_pregunta(PHONE) == ""


def test_pregunta_tiene_opciones():
    assert "1️⃣ Sí" in main.PREGUNTA_TABLA and "2️⃣ No" in main.PREGUNTA_TABLA
