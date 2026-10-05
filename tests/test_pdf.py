# PDFs: se escriben completos en /tmp y /download los entrega con sus bytes reales.
import glob
import os
from unittest.mock import patch

os.environ.setdefault("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_mensajes_largos import EXTRACCION_JAIME, MENSAJE_JAIME, json_claude, respuesta_claude  # noqa: E402

PRESUPUESTO = "/tmp/presupuesto_analisis.pdf"


def datos_presupuesto():
    with patch.object(main, "client") as cli:
        cli.messages.create.return_value = json_claude(EXTRACCION_JAIME)
        datos, faltan = main.extraer_presupuesto(MENSAJE_JAIME)
    assert faltan == []
    return datos


def generar_presupuesto_ok():
    with patch.object(main, "client") as cli:
        cli.messages.create.return_value = respuesta_claude("Consejo: separa el 10% para emergencias.")
        return main.generar_presupuesto(datos_presupuesto())


def temporales():
    return glob.glob("/tmp/*.pdf.*.tmp")


def test_presupuesto_se_guarda_completo_en_tmp():
    pdf_path, ok = generar_presupuesto_ok()
    assert pdf_path == PRESUPUESTO and ok
    with open(pdf_path, "rb") as f:
        contenido = f.read()
    assert len(contenido) > 1000 and contenido.startswith(b"%PDF-") and b"%%EOF" in contenido[-1024:]
    assert temporales() == []


def test_download_presupuesto_entrega_los_bytes_reales():
    generar_presupuesto_ok()
    with open(PRESUPUESTO, "rb") as f:
        esperado = f.read()
    r = main.app.test_client().get("/download/presupuesto_analisis.pdf", headers={"User-Agent": "TwilioProxy/1.1"})
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert int(r.headers["Content-Length"]) == len(esperado) > 0
    assert r.data == esperado
    assert "presupuesto_analisis.pdf" in r.headers["Content-Disposition"]


def test_download_con_pdf_vacio_da_404_y_no_200_vacio():
    open(PRESUPUESTO, "wb").close()
    r = main.app.test_client().get("/download/presupuesto_analisis.pdf")
    assert r.status_code == 404


def test_si_reportlab_falla_el_pdf_anterior_queda_intacto():
    generar_presupuesto_ok()
    with open(PRESUPUESTO, "rb") as f:
        anterior = f.read()
    with patch.object(main.SimpleDocTemplate, "build", side_effect=RuntimeError("falla reportlab")):
        with pytest.raises(RuntimeError):
            generar_presupuesto_ok()
    with open(PRESUPUESTO, "rb") as f:
        assert f.read() == anterior


def test_publicar_pdf_rechaza_un_temporal_vacio(tmp_path):
    final = tmp_path / "final.pdf"
    final.write_bytes(b"%PDF-1.4 bueno")
    temporal = tmp_path / "final.pdf.tmp"
    temporal.write_bytes(b"")
    with pytest.raises(ValueError):
        main.publicar_pdf(str(temporal), str(final))
    assert final.read_bytes() == b"%PDF-1.4 bueno"
    assert not temporal.exists()


def test_ya_no_existe_el_informe_de_gastos_de_ejemplo():
    # Era un PDF con datos inventados (Alimentos $150, Transporte $80...) igual para todos los usuarios
    assert not hasattr(main, "generar_informe_gastos")
    assert main.app.test_client().get("/download/informe_gastos.pdf").status_code == 404


def test_download_informe_metas_entrega_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "obtener_cuenta_activa", lambda *a, **k: "principal")
    ok, _ = main.registrar_meta("Ahorro", 1000, "2027-01-01", "savings", 250, telefono="593991234567")
    assert ok
    r = main.app.test_client().get("/download/informe_metas/593991234567.pdf")
    assert r.status_code == 200 and r.data.startswith(b"%PDF-")
    assert int(r.headers["Content-Length"]) == len(r.data) > 1000
