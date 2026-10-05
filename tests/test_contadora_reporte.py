# La Contadora (agents/reporter) muestra solo los movimientos guardados del usuario y, después
# de la tabla, SIEMPRE pregunta "¿Cómo quieres el reporte? 1 PDF / 2 Excel / 3 Solo verlo aquí".
# El PDF o Excel no se arma (ni se manda el link) hasta que conteste 1 o 2.
import inspect
import json
import os
from datetime import date
from unittest.mock import MagicMock

import pytest

import main
import reportes
from agents import reporter as agent_reporter

TEL = "whatsapp:+593991234567"
PHONE = main.normalizar_telefono(TEL)
HOY = date(2026, 10, 5)

PREGUNTA = ("¿Cómo quieres el reporte?\n1. PDF\n2. Excel\n3. Solo verlo aquí\n\n"
            "Responde con 1, 2 o 3")


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    guardar_real = main.guardar_memoria
    monkeypatch.setattr(main, "guardar_memoria",
                        lambda m, phone_clean="", cuenta="principal":
                        guardar_real(m, phone_clean, cuenta) if phone_clean else None)
    monkeypatch.setattr(main, "obtener_cuenta_activa", lambda *a, **k: "principal")
    monkeypatch.setattr(main, "memoria_usuarios", {})
    monkeypatch.setattr(main, "temp_gastos", {})
    monkeypatch.setattr(main, "temp_productos", {})
    monkeypatch.setattr(reportes, "hoy_fecha", lambda: HOY)
    claude = MagicMock()
    claude.messages.create.return_value.content = [MagicMock(text="Hola")]
    monkeypatch.setattr(main, "client", claude)
    return tmp_path


def enviar(texto):
    salida = main.Salida()
    main.procesar_mensaje(texto, TEL, "https://yoly.test", salida)
    return "\n".join(salida.textos)


def con_datos():
    main.guardar_gastos(TEL, [
        {"fecha": "2026-10-01", "descripcion": "Compra", "proveedor": "Ferretería Sol", "categoria": "materiales",
         "monto": 45.25},
        {"fecha": "2026-10-03", "descripcion": "Luz", "categoria": "servicios", "monto": 20},
    ])
    with open(os.path.join(main.obtener_ruta_datos(TEL), "ingresos.json"), "w") as f:
        json.dump([{"fecha": "2026-10-02", "descripcion": "Venta", "proveedor": "Cliente Ana", "monto": 300}], f)


def test_la_pregunta_es_exactamente_la_pedida():
    assert agent_reporter.PREGUNTA_REPORTE == PREGUNTA


@pytest.mark.parametrize("pedido", ["resumen de este mes", "informe de gastos", "reporte de octubre",
                                    "balance de octubre", "gastos de este mes"])
def test_siempre_pregunta_despues_de_la_tabla(entorno, pedido):
    con_datos()
    texto = enviar(pedido)
    assert "```" in texto                                  # hay tabla
    assert texto.endswith(PREGUNTA)
    assert texto.index("```") < texto.index("¿Cómo quieres el reporte?")
    assert "/download/" not in texto                       # ningún PDF/Excel todavía


def test_tabla_con_movimientos_reales(entorno):
    con_datos()
    texto = enviar("resumen de este mes")
    assert "Ferretería Sol" in texto and "Cliente Ana" in texto and "Luz" in texto
    assert "Ingreso" in texto and "Egreso" in texto
    assert "$45.25" in texto and "$300.00" in texto and "$20.00" in texto


def test_sin_datos_no_hay_tabla_ni_pregunta(entorno):
    texto = enviar("resumen de este mes")
    assert texto == "No hay transacciones aún"
    assert "```" not in texto and "$" not in texto
    assert main.reporte_pendiente(PHONE, "principal") is None


def test_1_manda_el_pdf_y_2_el_excel(entorno):
    con_datos()
    enviar("resumen de este mes")
    assert "/download/periodo/593991234567/pdf?desde=2026-10-01&hasta=2026-10-31" in enviar("1")
    assert main.reporte_pendiente(PHONE, "principal") is None
    enviar("resumen de este mes")
    assert "/download/periodo/593991234567/excel?desde=2026-10-01&hasta=2026-10-31" in enviar("2")


@pytest.mark.parametrize("respuesta", ["3", "nada", "Nada."])
def test_3_o_nada_termina_sin_archivos(entorno, respuesta):
    con_datos()
    enviar("resumen de este mes")
    texto = enviar(respuesta)
    assert "/download/" not in texto and "pdf?" not in texto
    assert main.reporte_pendiente(PHONE, "principal") is None
    main.client.messages.create.assert_not_called()


def test_la_contadora_no_suma_muestra_los_totales_que_le_dan():
    # Los totales vienen de reportes.resumir (Python); la Contadora solo los muestra
    movs = [{"fecha": HOY, "tipo": "gasto", "monto": 10.0, "descripcion": "A", "proveedor": "", "categoria": "x"},
            {"fecha": HOY, "tipo": "gasto", "monto": 5.0, "descripcion": "B", "proveedor": "", "categoria": "x"}]
    resumen = {"movimientos": movs, "total_ingresos": 0, "total_gastos": 99.0, "balance": -99.0}
    texto = agent_reporter.mensaje_reporte(resumen, "mes")
    assert "Egresos: $99.00" in texto and "$15.00" not in texto


def test_la_contadora_no_tiene_valores_de_ejemplo():
    codigo = inspect.getsource(agent_reporter.mensaje_reporte) + inspect.getsource(agent_reporter.tabla_transacciones)
    codigo += agent_reporter.PREGUNTA_REPORTE + agent_reporter.SIN_TRANSACCIONES
    for ejemplo in ("150", "80", "Alimentos", "Transporte", "Supermaxi"):
        assert ejemplo not in codigo
