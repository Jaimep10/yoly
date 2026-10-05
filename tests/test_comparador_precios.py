# Comparador de precios: se alimenta de las facturas de los usuarios y solo compara en la misma ciudad.
import json
from unittest.mock import MagicMock

import pytest

import main
import precios

TEL = "whatsapp:+593991234567"
PHONE = main.normalizar_telefono(TEL)
OTRO = "whatsapp:+593987654321"


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))

    # Mock memory with proper per-account tracking
    memoria_store = {}
    def mock_guardar_memoria(m, phone="", cuenta="principal"):
        if phone:
            key = f"{phone}_{cuenta}"
            memoria_store[key] = m
    def mock_cargar_memoria(phone="", cuenta="principal"):
        if phone:
            key = f"{phone}_{cuenta}"
            return memoria_store.get(key, {})
        return {}

    monkeypatch.setattr(main, "guardar_memoria", mock_guardar_memoria)
    monkeypatch.setattr(main, "cargar_memoria", mock_cargar_memoria)
    monkeypatch.setattr(main, "memoria_usuarios", {})
    monkeypatch.setattr(main, "temp_gastos", {})
    monkeypatch.setattr(main, "temp_productos", {})
    claude = MagicMock()
    claude.messages.create.return_value.content = [MagicMock(text="¿Qué necesitas?")]
    monkeypatch.setattr(main, "client", claude)
    return tmp_path


def enviar(texto, tel=TEL):
    salida = main.Salida()
    main.procesar_mensaje(texto, tel, "https://yoly.test", salida)
    return "\n".join(salida.textos)


def arroz(precio, marca="Balu", medida="2kg"):
    return {"producto": "Arroz blanco", "producto_norm": "arroz blanco", "marca": marca,
            "medida": medida, "precio": precio}


def aceite(precio):
    return {"producto": "Aceite girasol", "producto_norm": "aceite girasol", "marca": "La Favorita",
            "medida": "1 L", "precio": precio}


# ---------- normalización ----------

def test_ciudad_normalizada():
    assert precios.normalizar_ciudad("Quito, Pichincha") == ("quito", "Quito")
    assert precios.normalizar_ciudad("GYE")[0] == "guayaquil"
    assert precios.normalizar_ciudad("no especificada") == (None, None)
    assert precios.normalizar_ciudad("../../etc")[0] == "etc"


def test_medidas_equivalentes():
    assert precios.parsear_medida("2 kg") == precios.parsear_medida("2000g") == (2000.0, "g")
    assert precios.medida_texto(precios.parsear_medida("1 lt")) == "1L"
    assert precios.parsear_medida("arroz blanco") is None


def test_mismo_producto_exige_misma_medida():
    a = precios.limpiar_articulo(arroz(3.5))
    assert precios.mismo_producto(a, precios.limpiar_articulo(arroz(2.99, marca="Gustadina")))
    assert not precios.mismo_producto(a, precios.limpiar_articulo(arroz(6.0, medida="5kg")))
    assert not precios.mismo_producto(a, precios.limpiar_articulo(aceite(2.5)))


def test_articulo_sin_precio_no_se_usa():
    assert precios.limpiar_articulo({"producto": "Pan", "precio": None}) is None
    assert precios.limpiar_articulo({"producto": "Pan", "precio": "1,25"})["precio"] == 1.25


def test_consultas(entorno):
    base = str(entorno)
    assert precios.parsear_consulta(base, "Mi ciudad es Quito") == {"tipo": "ciudad", "ciudad": "Quito"}
    assert precios.parsear_consulta(base, "¿Dónde es más barato el arroz 2kg?") == \
        {"tipo": "buscar", "producto": "arroz 2kg", "ciudad": None, "todas": False}
    assert precios.parsear_consulta(base, "precio del aceite en Guayaquil")["ciudad"] == "guayaquil"
    assert precios.parsear_consulta(base, "precios de atun en aceite")["producto"] == "atun en aceite"
    assert precios.parsear_consulta(base, "precios de arroz en todas las ciudades")["todas"] is True
    assert precios.parsear_consulta(base, "tiendas baratas en Quito") == {"tipo": "ranking", "ciudad": "quito"}
    assert precios.parsear_consulta(base, "envié 500, renta 380") is None


# ---------- flujo con Yoly ----------

def test_factura_compara_con_otras_tiendas_de_la_misma_ciudad(entorno):
    main.registrar_precios_factura(OTRO, "Mi Comisariato", "Quito", [arroz(2.99), aceite(2.40)])
    main.registrar_precios_factura(OTRO, "Tía", "Guayaquil", [arroz(1.50)])  # otra ciudad: no cuenta

    texto = main.registrar_precios_factura(TEL, "Supermaxi", "Quito", [arroz(3.50), aceite(2.20)])

    assert "Guardé 2 precios de *Supermaxi*" in texto
    assert "en *Mi Comisariato* $2.99" in texto and "ahorras $0.51" in texto
    assert "Tía" not in texto and "1.50" not in texto
    assert "Aceite" not in texto  # el aceite estaba más barato en Supermaxi
    assert precios.ciudad_usuario(main.DATA_DIR, PHONE) == "Quito"


def test_misma_factura_dos_veces_no_duplica(entorno):
    main.registrar_precios_factura(TEL, "Supermaxi", "Quito", [arroz(3.50)], "2026-10-01")
    texto = main.registrar_precios_factura(TEL, "Supermaxi", "Quito", [arroz(3.50)], "2026-10-01")
    assert "ya la tenía" in texto
    assert len(precios.cargar_ciudad(main.DATA_DIR, "quito")) == 1


def test_historico_no_guarda_el_telefono(entorno):
    main.registrar_precios_factura(TEL, "Supermaxi", "Quito", [arroz(3.50)])
    contenido = (entorno / "precios" / "quito" / "articulos.json").read_text()
    assert "991234567" not in contenido


def test_donde_es_mas_barato_en_mi_ciudad(entorno):
    main.registrar_precios_factura(OTRO, "Mi Comisariato", "Quito", [arroz(2.99)])
    main.registrar_precios_factura(OTRO, "Fybeca", "Quito", [arroz(3.75)])
    main.registrar_precios_factura(OTRO, "Tía", "Guayaquil", [arroz(1.50)])
    main.registrar_precios_factura(TEL, "Supermaxi", "Quito", [arroz(3.50)])

    texto = enviar("¿dónde es más barato el arroz 2kg?")

    assert "en 3 tiendas de Quito" in texto
    assert texto.index("Mi Comisariato") < texto.index("Supermaxi") < texto.index("Fybeca")
    assert "más barato" in texto and "ahorras $0.76" in texto
    assert "Tía" not in texto
    assert "/dashboard/" + PHONE + "/precios" in texto


def test_precio_entre_ciudades(entorno):
    main.registrar_precios_factura(OTRO, "Mi Comisariato", "Quito", [arroz(2.99)])
    main.registrar_precios_factura(OTRO, "Tía", "Guayaquil", [arroz(1.50)])
    texto = enviar("precios de arroz en todas las ciudades")
    assert texto.index("Guayaquil") < texto.index("Quito")


def test_ranking_tiendas(entorno):
    main.registrar_precios_factura(OTRO, "Mi Comisariato", "Quito", [arroz(2.99), aceite(2.40)])
    main.registrar_precios_factura(OTRO, "Supermaxi", "Quito", [arroz(3.50), aceite(2.60)])
    texto = enviar("tiendas baratas en Quito")
    assert texto.index("Mi Comisariato") < texto.index("Supermaxi")
    assert "más barato que el promedio" in texto


def test_sin_ciudad_pregunta_y_luego_compara(entorno):
    main.registrar_precios_factura(OTRO, "Mi Comisariato", "Quito", [arroz(2.99)])

    texto = main.registrar_precios_factura(TEL, "Supermaxi", None, [arroz(3.50)])
    assert "mi ciudad es" in texto
    assert len(precios.cargar_ciudad(main.DATA_DIR, "quito")) == 1  # aún no se mezcla con nadie

    texto = enviar("mi ciudad es Quito")
    assert "tu ciudad es *Quito*" in texto
    assert "en *Mi Comisariato* $2.99" in texto
    assert len(precios.cargar_ciudad(main.DATA_DIR, "quito")) == 2


def test_foto_de_factura_de_compra_alimenta_comparador(entorno, monkeypatch):
    main.registrar_precios_factura(OTRO, "Mi Comisariato", "Quito", [arroz(2.99)])
    monkeypatch.setattr(main, "descargar_media_twilio", lambda url: b"img")
    monkeypatch.setattr(main, "convertir_a_webp", lambda b: b"webp")
    vision = {"cliente": "Supermaxi", "pagos": [{"fecha": "2026-10-03", "monto": 5.7}], "descripcion": "Compra",
              "tipo": "factura_compra", "tienda": "Supermaxi", "ciudad": "Quito",
              "articulos": [arroz(3.50), {"producto": "Leche", "precio": 1.20}, {"producto": "Pan", "precio": None}]}
    main.client.messages.create.return_value.content = [MagicMock(text=json.dumps(vision))]

    texto = main.procesar_foto_inteligente("https://media", TEL)

    assert "Leí Supermaxi: $6" in texto
    assert "Guardé 2 precios" in texto and "1 producto sin precio claro" in texto
    assert "en *Mi Comisariato* $2.99" in texto
    assert texto.endswith(main.PREGUNTA_TABLA)
    gastos = main.cargar_gastos(TEL)
    assert len(gastos) == 1 and gastos[0]["monto"] == 5.7


def test_ticket_con_varias_lineas_no_se_vuelve_libreta(entorno, monkeypatch):
    monkeypatch.setattr(main, "descargar_media_twilio", lambda url: b"img")
    monkeypatch.setattr(main, "convertir_a_webp", lambda b: b"webp")
    vision = {"cliente": "Tía", "pagos": [{"monto": 2}, {"monto": 3}], "tipo": "factura_compra",
              "tienda": "Tía", "ciudad": "Quito", "articulos": [arroz(2.0)]}
    main.client.messages.create.return_value.content = [MagicMock(text=json.dumps(vision))]
    main.procesar_foto_inteligente("https://media", TEL)
    assert main.obtener_cobro(PHONE) is None


def test_dashboard_comparativas(entorno):
    main.registrar_precios_factura(OTRO, "Mi Comisariato", "Quito", [arroz(2.99)])
    main.registrar_precios_factura(TEL, "<script>x</script>", "Quito", [arroz(3.50)])
    with main.app.test_client() as c:
        r = c.get(f"/dashboard/{PHONE}/precios")
    pagina = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Comparativas en Quito" in pagina and "Ahorrar aquí" in pagina
    assert "Mi+Comisariato+Quito" in pagina
    assert "<script>x</script>" not in pagina and "&lt;script&gt;" in pagina


def test_dashboard_sin_ciudad(entorno):
    with main.app.test_client() as c:
        r = c.get(f"/dashboard/{PHONE}/precios")
    assert r.status_code == 200 and "mi ciudad es Quito" in r.get_data(as_text=True)
