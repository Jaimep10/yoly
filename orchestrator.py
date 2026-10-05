# El jefe: OrquestadorYoly coordina a los 4 agentes con cada mensaje de fotos que llega por WhatsApp.
#
#   foto(s) -> Portero (agents/classifier) -> Ojo (agents/vision, una vez por foto)
#           -> Calculadora (agents/calculator) -> Contadora (agents/reporter) -> respuesta WA
#
# No toca Flask ni Twilio: main.py descarga las fotos y le da al jefe las "herramientas" para guardar
# (gastos, carpetas, precios, memoria) por medio de un Contexto.
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from agents import calculator as agent_calculator
from agents import classifier as agent_classifier
from agents import reporter as agent_reporter
from agents.calculator import Calculadora
from agents.classifier import Portero
from agents.guide import Guía
from agents.reporter import Contadora
from agents.vision import Ojo

logger = logging.getLogger(__name__)

ERROR_LECTURA = "❌ No pude procesar la factura. Asegúrate que sea una imagen clara."
ERROR_MONTO = "❌ No pude leer los números de la factura. Por favor, envía una imagen más clara o escribe el monto manualmente."
ERROR_TRANSFERENCIA = "❌ No pude leer el monto de la transferencia. Envía una captura más clara o escribe el monto."
YA_PROCESADA = "👍 Esa foto ya la había procesado, no la guardé otra vez."

# Ninguna foto puede trabar al resto: si Claude no contesta a tiempo, esa foto se salta.
TIMEOUT_FOTO = 30       # segundos para que el Ojo lea una foto
TIMEOUT_PORTERO = 20    # segundos para que el Portero diga qué son


class Demora(Exception):
    pass


def con_limite(segundos, funcion, *args):
    """Corre funcion(*args) y espera máximo `segundos`. Si tarda más, lanza Demora (el hilo queda solo)."""
    resultado = {}

    def correr():
        try:
            resultado["valor"] = funcion(*args)
        except Exception as e:
            resultado["error"] = e

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    hilo.join(segundos)
    if hilo.is_alive():
        raise Demora(f"tardó más de {segundos}s")
    if "error" in resultado:
        raise resultado["error"]
    return resultado.get("valor")


@dataclass
class Contexto:
    """Lo que el orquestador necesita del resto del bot (main.py se lo pasa)."""
    cliente: Any                                   # cliente de Anthropic
    modelo: str
    phone_clean: str
    guardar_imagen: Callable[[bytes], str]         # guarda la foto y devuelve su ruta
    guardar_cobro: Callable[[str, dict], None]     # escribe en memoria_global.json
    cobro_actual: Callable[[], Optional[dict]]     # cobro ya guardado del usuario (o None)
    guardar_gasto: Callable[..., str]              # (vision, pagos, total, ruta) -> texto
    guardar_transferencia: Callable[..., str]      # (vision, pagos, total, ruta, texto_usuario) -> texto
    huellas_vistas: Callable[[], list]
    marcar_vistas: Callable[[list], None]
    marcar_pregunta_tabla: Callable[[], None]


def ruta_documento(vision, tipo_portero):
    """Decide a qué camino va cada foto ya leída: transferencia, libretita o gasto (factura)."""
    tipo_vision = vision.get('tipo')
    if tipo_vision == 'transferencia':
        return agent_classifier.TRANSFERENCIA
    if tipo_vision == 'factura_compra':
        return agent_classifier.FACTURAS
    pagos, _ = agent_calculator.total_documento(vision)
    deuda = agent_calculator.a_numero(vision.get('deuda')) or 0
    if deuda > 0 or len(pagos) > 1:
        return agent_classifier.LIBRETITA
    if tipo_portero == agent_classifier.TRANSFERENCIA and not tipo_vision and pagos:
        return agent_classifier.TRANSFERENCIA
    return agent_classifier.FACTURAS


class OrquestadorYoly:
    """El jefe de los 4 agentes."""

    def __init__(self, herramientas=None):
        """herramientas(phone) -> Contexto: lo que main.py presta para guardar cada cosa en su lugar."""
        self.portero = Portero()
        self.ojo = Ojo()
        self.calculadora = Calculadora()
        self.contadora = Contadora()
        self.guia = Guía()
        self.herramientas = herramientas

    def handle_whatsapp(self, phone, images, texto="", info=None, ctx=None, fechas_foto=None):
        """
        phone: quien escribe. images: lista de fotos (webp) del mismo mensaje, 1 o varias. texto: lo que escribió.
        fechas_foto: fecha EXIF de cada foto (o None), respaldo si la factura no trae fecha impresa.
        Devuelve el texto de la respuesta para WhatsApp.
        0. Guía: si es una pregunta sin fotos, responde directamente.
        1. Portero: qué es, cuántas fotos, si es repetida.
        2. Ojo: lee cada foto (fecha, monto, método, cliente).
        3. Calculadora: suma en Python, saldo = deuda - pagado, duplicados por hash.
        4. Contadora: respuesta WA + tarjetas del dashboard (+ PDF y Excel en las descargas) y guarda.
        """
        # FIRST: Check if it's a question (no images)
        if not images and self.guia.es_pregunta(texto):
            return self.guia.responder(texto, phone, {})

        ctx = ctx or self.herramientas(phone)
        return self._procesar(list(images), texto or "", ctx, info, fechas_foto)

    def _procesar(self, imagenes, texto_usuario, ctx, info=None, fechas_foto=None):
        """
        imagenes: lista de fotos ya en webp (bytes). Devuelve el texto para WhatsApp.
        info (opcional): se anota info["tipo"] (lo que leyó Vision en la primera foto) e info["clasificacion"].
        """
        info = info if info is not None else {}

        # 1. Portero: tipo de cada foto y fotos repetidas
        vistas = ctx.huellas_vistas()
        try:
            clasificacion = con_limite(TIMEOUT_PORTERO, self.portero.clasificar,
                                       imagenes, texto_usuario, ctx.cliente, ctx.modelo, vistas)
        except Exception as e:
            # Sin Portero igual se sigue: el Ojo decide el tipo de cada foto
            logger.warning(f"Portero no contestó ({e}); sigo sin su clasificación")
            clasificacion = self.portero.clasificar(imagenes, texto_usuario, None, ctx.modelo, vistas)
        info['clasificacion'] = clasificacion
        logger.info(f"Portero: {clasificacion['tipo']} ({clasificacion['cantidad']} foto(s), confianza {clasificacion['confianza']})")
        if clasificacion['tipo'] == agent_classifier.DUPLICADO:
            info['tipo'] = agent_classifier.DUPLICADO
            return YA_PROCESADA

        # 2. Ojo: una lectura por foto nueva
        leidas = []   # (vision, ruta_foto, camino, huella)
        respuestas = []
        varias = len(imagenes) > 1
        for img_info in clasificacion['imagenes']:
            if img_info['duplicada']:
                respuestas.append(f"(La foto {img_info['indice'] + 1} es repetida, la salté.)")
                continue
            imagen = imagenes[img_info['indice']]
            ruta = ctx.guardar_imagen(imagen)
            numero = img_info['indice'] + 1
            try:
                vision = con_limite(TIMEOUT_FOTO, self.ojo.extraer, imagen, img_info['tipo'], ctx.cliente, ctx.modelo)
            except Exception as e:
                logger.warning(f"Ojo: foto {numero} de {len(imagenes)} falló: {e}")
                vision = None
            logger.info(f"Ojo: foto {numero} de {len(imagenes)} {'leída' if vision else 'sin leer'}")
            if not vision:
                respuestas.append(f"❌ No pude leer la foto {numero}. Mándala otra vez más clara."
                                  if varias else ERROR_LECTURA)
                continue
            if fechas_foto and img_info['indice'] < len(fechas_foto) and fechas_foto[img_info['indice']]:
                vision.setdefault('fecha_foto', fechas_foto[img_info['indice']])
            info.setdefault('tipo', vision.get('tipo'))
            leidas.append((vision, ruta, ruta_documento(vision, img_info['tipo']), img_info['huella']))

        info['caminos'] = [l[2] for l in leidas]
        procesadas = []
        preguntar_tabla = False

        # 3 y 4 para libretitas: todas las páginas son un solo registro de deuda
        libretas = [l for l in leidas if l[2] == agent_classifier.LIBRETITA]
        if libretas:
            cobro = self.calculadora.calcular([l[0] for l in libretas], agent_classifier.LIBRETITA, ctx.cobro_actual())
            reporte = self.contadora.generar(ctx.phone_clean, cobro, agent_classifier.LIBRETITA, guardar=ctx.guardar_cobro,
                                             factura_path=libretas[0][1], incluir_archivos=False)
            info['reporte'] = reporte
            respuestas.append(reporte['mensaje_wa'].replace("\n\n" + agent_reporter.PREGUNTA_TABLA, ""))
            procesadas += [l[3] for l in libretas]
            preguntar_tabla = True

        # Facturas y transferencias: cada foto es su propio movimiento
        for vision, ruta, camino, huella in leidas:
            if camino == agent_classifier.LIBRETITA:
                continue
            pagos, total = self.calculadora.total_documento(vision)
            if camino == agent_classifier.TRANSFERENCIA:
                if total <= 0:
                    respuestas.append(ERROR_TRANSFERENCIA)
                    continue
                respuestas.append(ctx.guardar_transferencia(vision, pagos, total, ruta, texto_usuario))
            else:
                if total <= 0:
                    respuestas.append(ERROR_MONTO)
                    continue
                respuestas.append(ctx.guardar_gasto(vision, pagos, total, ruta))
                preguntar_tabla = True
            procesadas.append(huella)

        if procesadas:
            ctx.marcar_vistas(procesadas)
        texto = "\n\n".join(r for r in respuestas if r)
        if preguntar_tabla:
            ctx.marcar_pregunta_tabla()
            texto += "\n\n" + agent_reporter.PREGUNTA_TABLA
        return texto or ERROR_LECTURA
