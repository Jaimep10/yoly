# Agente 1 - "El Portero": mira las fotos antes que nadie.
# Dice qué son (libretita de deuda, factura, transferencia), cuenta cuántas llegaron
# y para en seco las fotos repetidas (misma imagen ya procesada o mandada dos veces).
import base64
import hashlib
import json
import logging

logger = logging.getLogger(__name__)

LIBRETITA = "libretita_deuda"
FACTURAS = "facturas"
TRANSFERENCIA = "transferencia"
OTRO = "otro"
DUPLICADO = "duplicado"
TIPOS = (LIBRETITA, FACTURAS, TRANSFERENCIA, OTRO)

# El modelo a veces contesta con los nombres que usa el Ojo; los aceptamos también
ALIAS = {
    "libretita_deuda": LIBRETITA, "libreta_cobros": LIBRETITA, "libreta": LIBRETITA, "libretita": LIBRETITA,
    "cobro_deuda": LIBRETITA, "deuda": LIBRETITA,
    "facturas": FACTURAS, "factura": FACTURAS, "factura_compra": FACTURAS, "ticket": FACTURAS, "recibo": FACTURAS,
    "transferencia": TRANSFERENCIA, "deposito": TRANSFERENCIA, "comprobante": TRANSFERENCIA,
    "otro": OTRO,
}

PROMPT = """Eres el portero de un bot de finanzas. Te llegan {n} imagen(es), en orden.
Para CADA imagen di qué es:
- "libretita_deuda": cuaderno, tabla o lista escrita de pagos/abonos a una deuda
- "facturas": factura, ticket o recibo de una tienda con total
- "transferencia": comprobante de transferencia, depósito, Zelle o pago a una persona
- "otro": cualquier otra cosa

Devuelve SOLO JSON, sin explicaciones:
{{"tipos": ["libretita_deuda"], "confianza": 0.95}}
"tipos" tiene exactamente {n} elemento(s). "confianza" de 0 a 1."""


def huella_imagen(imagen_bytes):
    """Huella de la foto: la misma imagen siempre da la misma huella."""
    return hashlib.sha256(imagen_bytes or b"").hexdigest()[:16]


def normalizar_tipo(tipo):
    return ALIAS.get(str(tipo or "").strip().lower(), OTRO)


def leer_json(texto):
    """Saca el primer objeto JSON del texto (aunque venga entre ``` o con palabras alrededor)."""
    texto = (texto or "").strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        return None
    try:
        return json.loads(texto[inicio:fin + 1])
    except json.JSONDecodeError:
        return None


def preguntar_tipos(imagenes, cliente, modelo):
    """Una sola llamada corta a Claude para todas las fotos. Devuelve (tipos, confianza)."""
    contenido = [{"type": "image", "source": {"type": "base64", "media_type": "image/webp",
                                              "data": base64.standard_b64encode(img).decode("utf-8")}}
                 for img in imagenes]
    contenido.append({"type": "text", "text": PROMPT.format(n=len(imagenes))})
    respuesta = cliente.messages.create(model=modelo, max_tokens=200,
                                        messages=[{"role": "user", "content": contenido}])
    datos = leer_json(respuesta.content[0].text) or {}
    if isinstance(datos.get("tipos"), list):
        tipos = [normalizar_tipo(t) for t in datos["tipos"]]
    else:
        tipos = [normalizar_tipo(datos.get("tipo"))]
    tipos = (tipos + [tipos[-1] if tipos else OTRO] * len(imagenes))[:len(imagenes)]
    try:
        confianza = max(0.0, min(1.0, float(datos.get("confianza", 0.8))))
    except (TypeError, ValueError):
        confianza = 0.5
    return tipos, confianza


def clasificar_documentos(imagenes, cliente=None, modelo=None, vistas=()):
    """
    Entrada: lista de imágenes (bytes), el cliente de Anthropic y las huellas de fotos ya procesadas.
    Salida: {"tipo", "cantidad", "confianza", "imagenes": [{"indice", "huella", "tipo", "duplicada"}]}
    tipo = "duplicado" si TODAS las fotos ya se habían procesado (o son la misma repetida);
    si solo algunas lo son, esas se marcan duplicada=True y las demás siguen.
    """
    vistas = set(vistas or ())
    en_lote = set()
    imagenes_info = []
    for i, img in enumerate(imagenes):
        huella = huella_imagen(img)
        duplicada = huella in vistas or huella in en_lote
        en_lote.add(huella)
        imagenes_info.append({"indice": i, "huella": huella, "tipo": DUPLICADO if duplicada else OTRO,
                              "duplicada": duplicada})

    nuevas = [info for info in imagenes_info if not info["duplicada"]]
    resultado = {"tipo": DUPLICADO, "cantidad": len(imagenes), "confianza": 1.0, "imagenes": imagenes_info}
    if not nuevas:
        return resultado

    tipos, confianza = [OTRO] * len(nuevas), 0.0
    if cliente is not None:
        try:
            tipos, confianza = preguntar_tipos([imagenes[info["indice"]] for info in nuevas], cliente, modelo)
        except Exception as e:
            # Si el portero falla, el Ojo igual lee la foto y decide el tipo
            logger.warning(f"Clasificador no pudo decidir el tipo: {e}")
    for info, tipo in zip(nuevas, tipos):
        info["tipo"] = tipo

    # Tipo general: el más común entre las fotos nuevas (empate: el de la primera)
    conteo = [info["tipo"] for info in nuevas]
    resultado["tipo"] = max(dict.fromkeys(conteo), key=conteo.count)
    resultado["confianza"] = confianza
    return resultado
