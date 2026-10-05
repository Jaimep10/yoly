# Agente 2 - "El Ojo": SOLO Claude Vision. Lee la foto y devuelve JSON.
# No suma, no calcula saldos, no guarda nada: eso es de la Calculadora y la Contadora.
import base64
import logging

from agents.classifier import FACTURAS, LIBRETITA, TRANSFERENCIA, leer_json

logger = logging.getLogger(__name__)

PROMPT = """Eres OCR de libretita de cobros y facturas. Extrae TODO lo que veas en la imagen.

Devuelve SOLO JSON valido, sin explicaciones, con esta forma (los valores <...> son
marcadores: reemplazalos por lo que VES en la imagen, o null si no aparece):

{
  "cliente": <nombre escrito en la imagen o null>,
  "deuda": <deuda total escrita o null>,
  "pagos": [
    {"fecha": <YYYY-MM-DD o null>, "monto": <numero leido>, "metodo": <metodo o "no especificado">, "nota": ""}
  ],
  "descripcion": <resumen breve de que es>,
  "tipo": <"libreta_cobros" | "factura_compra" | "transferencia" | "otro">,
  "tienda": null,
  "ciudad": null,
  "articulos": []
}

NUNCA copies valores de ejemplo: cada nombre, fecha y monto tiene que estar escrito en la foto.
Si un monto no se lee claro, NO lo pongas en "pagos" (mejor vacio que inventado).

Si es una FACTURA o TICKET DE COMPRA con productos (supermercado, farmacia, ferretería...):
  "tipo": "factura_compra",
  "tienda": <nombre impreso de la tienda>,
  "ciudad": <ciudad impresa o null>,
  "fecha": <YYYY-MM-DD o null>,
  "fecha_texto": <fecha tal cual impresa o null>,
  "articulos": [
    {"producto": <texto>, "producto_norm": <texto>, "marca": <texto o "">, "medida": <texto o "">, "cantidad": <numero>, "precio": <numero o null>, "categoria": <texto>}
  ]
y en "pagos" pon el TOTAL de la factura como un solo pago, con la fecha de la factura.

FECHA DE LA FACTURA (muy importante): busca la fecha IMPRESA en el ticket. Suele estar arriba o abajo,
junto a palabras como "Fecha", "Date", "Fecha de emisión", "Emitido", "Hora", "Time", o sola junto a la hora
(ej: "10/04/2026 14:32"). Puede venir como 04/10/2026, 04-10-2026, 04.10.2026, 04/10/26, 2026-10-04,
"4 de octubre 2026", "October 4, 2026", "Oct 4, 26", "04-OCT-2026".
  "fecha_texto": la fecha TAL CUAL está impresa (ej: "10/04/26").
  "fecha": la misma fecha en formato YYYY-MM-DD. Si los números son ambiguos (04/10/2026), decide por el
  país de la tienda: tiendas de USA (dirección con estado y ZIP, ej "NY 10035") usan MES/DIA;
  Ecuador y Latinoamérica usan DIA/MES.
  Si no ves ninguna fecha impresa, pon "fecha": null y "fecha_texto": null. NO uses la fecha de hoy ni la inventes.

Si es una TRANSFERENCIA, deposito o comprobante de pago (captura de la app del banco, Zelle, Venmo, recibo de pago a una persona):
  "tipo": "transferencia",
  "concepto": "el concepto, motivo, descripcion o memo TAL CUAL aparece, o null",
  "beneficiario": "a quien se le envio el dinero",
  "ordenante": "quien envio el dinero",
  "direccion": "recibida" si el dinero ENTRO a la cuenta del dueño del celular (palabras como "Recibiste",
     "Te enviaron", "Te transfirieron", "Depósito recibido", "Abono a tu cuenta", "Crédito", monto con "+");
     "enviada" si SALIO (palabras como "Enviaste", "Transferiste", "Pagaste", "Pago realizado", "Débito",
     monto con "-"); null si la imagen no lo dice. No lo adivines por el nombre de las personas,
  "banco": "banco o app",
  "referencia": "numero de comprobante si aparece",
y en "pagos" pon el monto como un solo pago con la fecha de la transferencia. No inventes el concepto: si no aparece, null.

Reglas:
- pagos: un objeto por cada pago individual, en el mismo orden de la imagen.
- fecha: en facturas, la fecha impresa (ver arriba). En libretitas: extrae de la libretita "01-3-26" -> "2026-01-03" (formato YYYY-MM-DD). Si no hay fecha, usa null pero NO inventes.
- monto: numero sin simbolo.
- metodo: busca palabras clave en la misma linea: "transf", "transferencia", "T", "efectivo", "efec", "E", "cheque", "chq", "deposito", "zelle". Si dice "200 T" es transferencia. Si solo dice "200", metodo = "no especificado".
- nota: si hay nota como "banco X" guardala en nota.
- deuda: la deuda original/total, solo si aparece en la imagen.
- Si es una factura o ticket normal con un solo total, pon ese total como un solo pago.
- tipo: "libreta_cobros" (cuaderno de pagos/deudas), "factura_compra" (ticket con productos y precios), "transferencia" (comprobante de transferencia o pago a una persona) u "otro".
- articulos: SOLO en factura_compra, una linea por producto. precio = precio UNITARIO (si dice "2 x 1.75  3.50", precio 1.75 y cantidad 2). producto_norm en minusculas, sin acentos, sin marca ni medida. marca y medida solo si aparecen (si no, ""). Si el precio de un producto no se lee claro, pon precio null.
- ciudad: solo si aparece impresa en la factura (direccion de la tienda). Si no aparece, null. No la adivines.

No sumes. Solo extrae. NUNCA inventes numeros que no esten en la imagen."""

PISTAS = {
    LIBRETITA: "Pista: parece una libretita de pagos de una deuda (tipo libreta_cobros).",
    FACTURAS: "Pista: parece una factura o ticket de tienda.",
    TRANSFERENCIA: "Pista: parece un comprobante de transferencia (tipo transferencia).",
}


def extraer_json(imagen, tipo=None, cliente=None, modelo=None):
    """
    Entrada: imagen en bytes (webp) y el tipo que dijo el Portero (solo como pista).
    Salida: dict con cliente, deuda, pagos [{fecha, monto, metodo, nota}], tipo, tienda, ciudad,
    articulos, concepto... tal como lo leyó Vision. None si no devolvió JSON válido.
    """
    texto = PROMPT
    if tipo in PISTAS:
        texto += "\n\n" + PISTAS[tipo] + " Si la imagen dice otra cosa, manda lo que ves."
    respuesta = cliente.messages.create(
        model=modelo,
        max_tokens=3000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/webp",
                                         "data": base64.standard_b64encode(imagen).decode("utf-8")}},
            {"type": "text", "text": texto},
        ]}],
    )
    datos = leer_json(respuesta.content[0].text)
    if not isinstance(datos, dict) or not datos:
        logger.warning(f"Vision no devolvió JSON válido: {respuesta.content[0].text[:300]!r}")
        return None
    # Registros con el nombre viejo del campo siguen funcionando
    if datos.get('deuda') in (None, "", 0) and datos.get('deuda_total') not in (None, ""):
        datos['deuda'] = datos['deuda_total']
    datos.pop('deuda_total', None)
    return datos


class Ojo:
    """Agente 2: lee una foto con Claude Vision y devuelve JSON (fecha, monto, metodo, cliente...)."""

    def extraer(self, img, tipo=None, cliente=None, modelo=None):
        return extraer_json(img, tipo, cliente, modelo)
