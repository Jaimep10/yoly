# Lo que el usuario escribe ("pagué 50 de luz") se guarda en disco, en su carpeta, y sigue ahí
# después de un reinicio. Antes "pagué" caía en las metas y no se guardaba nada.
import importlib
import json
import os
from unittest.mock import MagicMock

import pytest

import almacen
import main
import registro_texto

TEL = "whatsapp:+593991234567"
PHONE = main.normalizar_telefono(TEL)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "memoria_usuarios", {})
    monkeypatch.setattr(main, "temp_gastos", {})
    monkeypatch.setattr(main, "temp_productos", {})
    claude = MagicMock()
    claude.messages.create.return_value.content = [MagicMock(text="¿Qué necesitas?")]
    monkeypatch.setattr(main, "client", claude)
    return tmp_path


def enviar(texto):
    salida = main.Salida()
    main.procesar_mensaje(texto, TEL, "https://yoly.test", salida)
    return "\n".join(salida.textos)


def leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def test_pague_luz_se_guarda_en_disco_y_en_su_carpeta(entorno):
    respuesta = enviar("pagué 50 de luz")
    assert "Anotado" in respuesta and "$50.00" in respuesta
    base = entorno / PHONE / "principal"
    gastos = leer(base / "gastos.json")
    assert gastos[-1]["monto"] == 50 and gastos[-1]["rubro"] == "luz"
    carpeta = leer(base / "servicios" / "transacciones.json")
    assert carpeta[-1]["concepto"] == "Luz"


def test_los_datos_siguen_despues_de_reiniciar(entorno, monkeypatch):
    enviar("pagué 50 de luz")
    enviar("pagué 70 de la luz")
    # "Reinicio": se borra todo lo que estaba en RAM; solo queda lo del disco
    monkeypatch.setattr(main, "memoria_usuarios", {})
    respuesta = enviar("¿cuánto he pagado de luz?")
    assert "$120.00" in respuesta and "2 pagos" in respuesta


def test_lleva_la_cuenta_por_rubro_o_deuda(entorno):
    enviar("abono 100 a la deuda del banco")
    respuesta = enviar("abono 150 a la deuda del banco")
    assert "$250.00" in respuesta
    assert "Deudas" in respuesta
    assert "$250.00" in enviar("cuanto llevo pagado de la deuda del banco")


def test_ingresos_y_egresos_en_carpetas(entorno):
    enviar("recibí 500 de sueldo")
    enviar("gasté 30 en comida")
    enviar("pagué 50 de luz")
    respuesta = enviar("mis carpetas")
    assert "Ingresos: $500.00" in respuesta
    assert "Egresos: $80.00" in respuesta
    assert "Balance: $420.00" in respuesta
    assert "Comida" in respuesta and "Luz" in respuesta
    base = entorno / PHONE / "principal"
    assert leer(base / "ingresos.json")[-1]["monto"] == 500
    assert leer(base / "gastos_generales" / "transacciones.json")[-1]["rubro"] == "comida"


def test_pago_a_la_deuda_guardada_baja_lo_que_falta(entorno):
    main.guardar_cobro(PHONE, {"tipo": "cobro_deuda", "cliente": "Alan", "deuda": 1000,
                               "pagos": [{"fecha": "2026-10-01", "monto": 300}]}, "principal")
    respuesta = enviar("pagué 200 a la deuda de Alan")
    assert "Te falta: $500.00" in respuesta
    assert main.obtener_cobro(PHONE)["pagado"] == 500
    respuesta = enviar("borrar último")
    assert "borré" in respuesta
    assert main.obtener_cobro(PHONE)["pagado"] == 300


def test_borrar_ultimo(entorno):
    enviar("pagué 50 de luz")
    enviar("pagué 99 de agua")
    assert "$99.00" in enviar("borrar último")
    gastos = leer(entorno / PHONE / "principal" / "gastos.json")
    assert [g["monto"] for g in gastos] == [50]


def test_preguntas_y_planes_no_se_registran(entorno):
    enviar("tengo que pagar 50 de luz")
    enviar("quiero ahorrar 100")
    assert not os.path.exists(entorno / PHONE / "principal" / "gastos.json")


def test_sin_concepto_va_a_por_revisar(entorno):
    respuesta = enviar("pagué 200")
    assert "Por revisar" in respuesta
    assert leer(entorno / PHONE / "principal" / "por_revisar" / "transacciones.json")[-1]["revisar"] is True


def test_varios_montos_sigue_siendo_desglose(entorno):
    respuesta = enviar("Envié 500, renta 380, comida 120")
    assert "SI" in respuesta and "Anotado" not in respuesta


@pytest.mark.parametrize("texto,monto,rubro,movimiento", [
    ("pagué 50 de luz", 50, "luz", "gasto"),
    ("Pagué la luz 50", 50, "luz", "gasto"),
    ("pagué $1,200.50 de renta en efectivo", 1200.5, "renta", "gasto"),
    ("pague 25,50 de agua el 3/10", 25.5, "agua", "gasto"),
    ("me pagaron 300 por el trabajo de pintura", 300, "trabajo pintura", "ingreso"),
    ("compré gasolina 15 con tarjeta", 15, "gasolina", "gasto"),
])
def test_parsear_registro(texto, monto, rubro, movimiento):
    r = registro_texto.parsear_registro(texto)
    assert (r["monto"], r["rubro"], r["movimiento"]) == (monto, rubro, movimiento)


def test_data_dir_usa_variable_de_entorno(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "disco"))
    assert almacen.elegir_data_dir() == str(tmp_path / "disco")
    estado = almacen.revisar(almacen.elegir_data_dir())
    assert estado["escribible"]


def test_en_render_sin_disco_avisa_que_no_es_persistente(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("YOLY_DISCO_PERSISTENTE", raising=False)
    assert almacen.revisar(str(tmp_path))["persistente"] == (almacen._punto_de_montaje(str(tmp_path)) != '/')
    monkeypatch.setenv("YOLY_DISCO_PERSISTENTE", "1")
    assert almacen.revisar(str(tmp_path))["persistente"] is True


def test_ya_no_hay_rutas_fijas_de_otra_maquina():
    for archivo in ("main.py", "app/core/state.py", "app/bot/guia.py", "app/generators/excel_pro.py"):
        with open(os.path.join(almacen.RAIZ_APP, archivo), encoding="utf-8") as f:
            codigo = f.read()
        assert "'/home/claude/yoly" not in codigo and "'/app/data" not in codigo
