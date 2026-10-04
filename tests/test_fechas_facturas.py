# Facturas de tienda: la fecha impresa se lee en cualquier formato (04/10/2026, 04.10.2026,
# "October 4, 2026"...) y, si no hay, se usa la de la foto (EXIF) o la del mensaje marcada como aproximada.
import io
import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from PIL import Image

import main
from agents.calculator import normalizar_fecha, normalizar_pagos

TEL = "whatsapp:+593991234567"
PHONE = main.normalizar_telefono(TEL)
HOY = datetime(2026, 10, 4)


@pytest.mark.parametrize("texto, esperada", [
    ("2026-10-04", "2026-10-04"),
    ("04/10/2026", "2026-10-04"),
    ("04-10-2026", "2026-10-04"),
    ("04.10.2026", "2026-10-04"),
    ("04/10/26", "2026-10-04"),
    ("Fecha: 04/10/2026 14:32", "2026-10-04"),
    ("Fecha de emisión: 25/09/2026", "2026-09-25"),
    ("09/25/2026", "2026-09-25"),          # USA: el 25 no puede ser mes
    ("10/03/2026", "2026-03-10"),          # día/mes por defecto
    ("10/04/2026", "2026-04-10"),          # ambas en el pasado: día/mes
    ("12/09/2026", "2026-09-12"),
    ("09/12/2026", "2026-09-12"),          # día/mes sería diciembre (futuro) -> mes/día
    ("4 de octubre 2026", "2026-10-04"),
    ("4 de octubre de 2026", "2026-10-04"),
    ("October 4, 2026", "2026-10-04"),
    ("Oct 4, 26", "2026-10-04"),
    ("04-OCT-2026", "2026-10-04"),
    ("Sep 28 2026 5:41 PM", "2026-09-28"),
    ("2026-12-09", "2026-09-12"),          # Vision cambió día y mes: diciembre aún no llega
])
def test_normalizar_fecha(texto, esperada):
    assert normalizar_fecha(texto, hoy=HOY) == esperada


@pytest.mark.parametrize("texto", [None, "", "sin fecha", "31/02/2026", "Total 12.50"])
def test_fecha_que_no_se_entiende_queda_vacia(texto):
    assert normalizar_fecha(texto, hoy=HOY) == ""


def test_pagos_ya_no_pierden_la_fecha_con_barras():
    pagos = normalizar_pagos([{"fecha": "25/09/2026", "monto": 12.5}])
    assert pagos[0]["fecha"] == "2026-09-25"


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "guardar_memoria", lambda m: None)
    monkeypatch.setattr(main, "memoria_usuarios", {})
    monkeypatch.setattr(main, "descargar_media_twilio", lambda url: b"img")
    monkeypatch.setattr(main, "convertir_a_webp", lambda b: b"webp")
    monkeypatch.setattr(main, "registrar_precios_factura", lambda *a, **k: "")
    monkeypatch.setattr(main, "client", MagicMock())
    return tmp_path


def factura(**campos):
    datos = {"tipo": "factura_compra", "tienda": "C-Town", "cliente": "C-Town", "descripcion": "Compra",
             "pagos": [{"fecha": None, "monto": 23.4}], "articulos": []}
    datos.update(campos)
    return datos


def foto(vision):
    main.client.messages.create.return_value.content = [MagicMock(text=json.dumps(vision))]
    respuesta = main.procesar_foto_inteligente("https://media", TEL, "")
    return respuesta, main.cargar_gastos(TEL)[-1]


def test_factura_con_fecha_impresa_con_barras(entorno):
    respuesta, gasto = foto(factura(pagos=[{"fecha": "25/09/2026", "monto": 23.4}]))
    assert gasto["fecha"] == "2026-09-25"
    assert gasto["fecha_origen"] == "factura" and not gasto["fecha_aproximada"]
    assert "25/09/2026" in respuesta and "aproximada" not in respuesta


def test_factura_usa_fecha_texto_si_el_pago_no_trae(entorno):
    _, gasto = foto(factura(fecha=None, fecha_texto="Sep 28, 2026"))
    assert gasto["fecha"] == "2026-09-28" and not gasto["fecha_aproximada"]


def test_sin_fecha_impresa_usa_la_de_la_foto(entorno, monkeypatch):
    jpg = io.BytesIO()
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = "2026:09:20 18:05:00"
    Image.new("RGB", (8, 8)).save(jpg, format="JPEG", exif=exif)
    monkeypatch.setattr(main, "descargar_media_twilio", lambda url: jpg.getvalue())
    respuesta, gasto = foto(factura())
    assert gasto["fecha"] == "2026-09-20"
    assert gasto["fecha_origen"] == "foto" and gasto["fecha_aproximada"]
    assert "aproximada" in respuesta


def test_sin_fecha_ni_exif_usa_hoy_y_queda_aproximada(entorno):
    respuesta, gasto = foto(factura())
    assert gasto["fecha"] == datetime.now().strftime("%Y-%m-%d")
    assert gasto["fecha_origen"] == "mensaje" and gasto["fecha_aproximada"]
    assert "usé hoy" in respuesta


def test_panel_muestra_badge_de_fecha_aproximada(entorno):
    foto(factura())
    html = main.app.test_client().get(f"/dashboard/{PHONE}").get_data(as_text=True)
    assert "📅 Fecha aproximada" in html
