# Pruebas sin red: Anthropic y Twilio van mockeados.
import os
os.environ["MPLBACKEND"] = "Agg"  # antes de importar main/matplotlib: sin ventanas en Mac
import re
from unittest.mock import MagicMock, patch

os.environ.setdefault("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

import main  # noqa: E402

MENSAJE_JAIME = (
    "gano 17 dolares la hora trabajo 6 horas diarias de lunes a viernes ademas gano 400 semanales "
    "de otro ingreso y en mis gastos oaho 380 rebta en quito mensual, 120 comida semanal, pago 35 a la "
    "semana de plataformas y suscripciones gasto 100 al mes aprox. de seguro de gimnasio 53, de telefono "
    "85 de arriendo new york 500 y en varios 100"
)


def respuesta_claude(texto):
    r = MagicMock()
    r.content = [MagicMock(text=texto)]
    return r


def enviar(texto):
    data = {"From": "whatsapp:+1234567890", "Body": texto, "MessageSid": "test"}
    with main.app.test_request_context("/whatsapp", method="POST", data=data):
        return main.whatsapp()


def mensajes_twiml(xml):
    return re.findall(r"<Message>(.*?)</Message>", xml, re.S)


def test_partir_mensaje_corto_no_se_divide():
    assert main.partir_mensaje("hola") == ["hola"]


def test_partir_mensaje_largo_respeta_limite_y_no_pierde_texto():
    texto = ("Línea con consejo financiero número uno. " * 10 + "\n") * 6  # ~2500 caracteres
    partes = main.partir_mensaje(texto)
    assert len(partes) >= 2
    assert all(len(p) <= main.LIMITE_WHATSAPP for p in partes)
    assert "".join(partes).replace(" ", "").replace("\n", "") == texto.replace(" ", "").replace("\n", "")


def test_partir_mensaje_sin_espacios():
    partes = main.partir_mensaje("x" * 3200)
    assert [len(p) for p in partes] == [1500, 1500, 200]


def test_modelo_en_las_tres_llamadas():
    fuente = open(main.__file__, encoding="utf-8").read()
    assert fuente.count("model=MODELO_CLAUDE") == 4  # 3 originales + extracción del presupuesto
    assert "claude-3" not in fuente.replace("claude-3-5-haiku y claude-3-haiku", "")
    assert main.MODELO_CLAUDE == "claude-haiku-4-5"


def test_respuesta_de_2000_caracteres_sale_en_varios_mensajes(capsys):
    larga = ("Te recomiendo separar un fondo de emergencia cada mes. " * 8 + "\n") * 5
    assert len(larga) > 2000
    with patch.object(main, "client") as cli:
        cli.messages.create.return_value = respuesta_claude(larga)
        xml = enviar("Hola, explícame con detalle cómo organizar mis finanzas este año")
        assert cli.messages.create.call_args.kwargs["model"] == "claude-haiku-4-5"
    msgs = mensajes_twiml(xml)
    assert len(msgs) >= 2
    assert all(len(m) <= main.LIMITE_WHATSAPP for m in msgs)
    out = capsys.readouterr().out
    assert "Pregunta recibida:" in out
    assert f"Respuesta generada: {len(larga)} caracteres" in out


import json

EXTRACCION_JAIME = {
    "ingresos": [
        {"concepto": "trabajo por hora", "monto": 17, "periodo": "hora", "horas_por_dia": 6, "dias_por_semana": 5},
        {"concepto": "otro ingreso", "monto": 400, "periodo": "semana", "horas_por_dia": 0, "dias_por_semana": 0},
    ],
    "gastos": [
        {"concepto": "renta Quito", "monto": 380, "periodo": "mes", "es_deuda": False},
        {"concepto": "comida", "monto": 120, "periodo": "semana", "es_deuda": False},
        {"concepto": "arriendo New York", "monto": 500, "periodo": "mes", "es_deuda": False},
    ],
}


def json_claude(obj):
    r = MagicMock()
    r.content = [MagicMock(type="text", text=json.dumps(obj))]
    return r


def test_extraer_presupuesto_usa_los_numeros_del_usuario():
    with patch.object(main, "client") as cli:
        cli.messages.create.return_value = json_claude(EXTRACCION_JAIME)
        datos, faltan = main.extraer_presupuesto(MENSAJE_JAIME)
    assert faltan == []
    # 17 x 6 h x 5 días x 52/12 + 400 x 52/12
    assert datos["ingreso"] == round(17 * 6 * 5 * 52 / 12 + 400 * 52 / 12, 2)
    assert datos["gastos_fijos"]["comida"] == round(120 * 52 / 12, 2)
    assert datos["deudas"] == {}


def test_si_falta_horas_pregunta_en_vez_de_inventar():
    sin_horas = json.loads(json.dumps(EXTRACCION_JAIME))
    sin_horas["ingresos"][0]["horas_por_dia"] = 0
    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.return_value = json_claude(sin_horas)
        xml = enviar("gano 17 la hora y 400 semanales, mis gastos son 380 de renta")
    assert not tw.messages.create.called
    assert "horas al día" in xml


def test_mensaje_largo_de_jaime_no_se_bloquea(capsys):
    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.side_effect = [json_claude(EXTRACCION_JAIME), respuesta_claude("Consejo corto.")]
        xml = enviar(MENSAJE_JAIME)
    assert tw.messages.create.called  # manda el PDF por Twilio
    msgs = mensajes_twiml(xml)
    assert len(msgs) == 1 and msgs[0].startswith("✅")
    assert f"Pregunta recibida: {len(MENSAJE_JAIME)} caracteres" in capsys.readouterr().out


def test_sin_consejo_no_dice_que_si_lo_incluye():
    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.side_effect = [json_claude(EXTRACCION_JAIME), Exception("401 API key is invalid")]
        xml = enviar(MENSAJE_JAIME)
    msgs = mensajes_twiml(xml)
    assert "✅" not in xml and "no pude generar los consejos" in msgs[0]
    assert "recomendaciones" not in tw.messages.create.call_args.kwargs["body"]


def test_si_twilio_falla_no_dice_enviado():
    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.side_effect = [json_claude(EXTRACCION_JAIME), respuesta_claude("Consejo.")]
        tw.messages.create.side_effect = Exception("Twilio caído")
        xml = enviar(MENSAJE_JAIME)
    assert "✅" not in xml and "Error" in xml


# ---------- Claude lento: el webhook no puede pasar los 15 s de Twilio ----------
import time


def esperar_envios(tw, minimo=1, limite=5.0):
    fin = time.time() + limite
    while tw.messages.create.call_count < minimo and time.time() < fin:
        time.sleep(0.02)
    return [c.kwargs for c in tw.messages.create.call_args_list]


def claude_lento(texto, segundos):
    def _create(**kwargs):
        time.sleep(segundos)
        return respuesta_claude(texto)
    return _create


def test_claude_lento_contesta_al_tiro_y_manda_la_respuesta_despues(monkeypatch):
    monkeypatch.setattr(main, "ESPERA_MAX_SEGUNDOS", 0.2)
    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.side_effect = claude_lento("Respuesta que tardó mucho.", 1.0)
        t = time.time()
        xml = enviar("Hola, explícame con detalle cómo organizar mis finanzas este año")
        assert time.time() - t < 0.8  # no espera a Claude
        assert mensajes_twiml(xml) == [main.MENSAJE_ESPERA]
        envios = esperar_envios(tw)
    assert envios == [{"from_": "whatsapp:+14155238886", "to": "whatsapp:+1234567890", "body": "Respuesta que tardó mucho."}]


def test_respuesta_tardia_larga_sale_partida(monkeypatch):
    monkeypatch.setattr(main, "ESPERA_MAX_SEGUNDOS", 0.1)
    larga = ("Te recomiendo separar un fondo de emergencia cada mes. " * 8 + "\n") * 5
    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.side_effect = claude_lento(larga, 0.4)
        enviar("Explícame todo sobre mis finanzas")
        envios = esperar_envios(tw, minimo=2)
    assert len(envios) >= 2 and all(len(e["body"]) <= main.LIMITE_WHATSAPP for e in envios)


def test_error_tardio_tambien_le_llega_al_usuario(monkeypatch):
    monkeypatch.setattr(main, "ESPERA_MAX_SEGUNDOS", 0.1)

    def falla_lenta(**kwargs):
        time.sleep(0.4)
        raise Exception("timeout de la API")

    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.side_effect = falla_lenta
        xml = enviar("Explícame todo sobre mis finanzas")
        envios = esperar_envios(tw)
    assert mensajes_twiml(xml) == [main.MENSAJE_ESPERA]
    assert "error" in envios[0]["body"].lower()


def test_respuesta_rapida_no_manda_mensaje_de_espera():
    with patch.object(main, "client") as cli, patch.object(main, "twilio_client") as tw:
        cli.messages.create.return_value = respuesta_claude("Hola, soy Yoly.")
        xml = enviar("hola")
    assert mensajes_twiml(xml) == ["Hola, soy Yoly."]
    assert not tw.messages.create.called


def test_cliente_anthropic_tiene_timeout_corto():
    fuente = open(main.__file__, encoding="utf-8").read()
    assert "timeout=60.0, max_retries=1" in fuente
