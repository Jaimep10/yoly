# Junta las fotos que llegan "en grupo" por WhatsApp.
#
# Twilio NO manda un grupo de fotos en un solo webhook: manda un webhook por foto, casi al mismo
# tiempo. Antes cada uno arrancaba su propio proceso (Portero + Vision + guardar) y chocaban:
# se acababan los hilos de gunicorn, Twilio cortaba a los 15 s y los archivos JSON se pisaban.
#
# Ahora cada foto entra a un lote por teléfono. Cuando pasan unos segundos sin fotos nuevas, el
# lote se cierra y UN solo hilo lo procesa. Un candado por teléfono hace que dos lotes del mismo
# usuario nunca se procesen a la vez. Funciona porque gunicorn corre un solo proceso (Procfile sin -w).
import logging
import threading
import time
import uuid

logger = logging.getLogger(__name__)

MAX_FOTOS = 5            # fotos por lote; las demás se piden en otro mensaje
ESPERA_LOTE = 3.0        # segundos sin fotos nuevas para cerrar el lote
ESPERA_LOTE_MAX = 10.0   # el lote se cierra igual pasado este tiempo desde la primera foto


class LoteFotos:
    """
    procesar(lote) se llama en un hilo aparte con un dict:
      {"id", "telefono", "urls", "textos", "extra", "sobrantes"}
    urls: máximo MAX_FOTOS, en el orden en que llegaron. sobrantes: cuántas fotos no entraron.
    """

    def __init__(self, procesar, espera=ESPERA_LOTE, espera_max=ESPERA_LOTE_MAX, max_fotos=MAX_FOTOS):
        self.procesar = procesar
        self.espera = espera
        self.espera_max = espera_max
        self.max_fotos = max_fotos
        self._abiertos = {}                  # telefono -> lote que sigue recibiendo fotos
        self._candado = threading.Lock()
        self._por_telefono = {}              # telefono -> Lock (un lote a la vez por usuario)

    def agregar(self, telefono, urls, texto="", extra=None):
        """Mete las fotos al lote del teléfono. Devuelve True si con esto empezó un lote nuevo."""
        ahora = time.monotonic()
        with self._candado:
            lote = self._abiertos.get(telefono)
            nuevo = lote is None
            if nuevo:
                lote = {"id": uuid.uuid4().hex[:8], "telefono": telefono, "urls": [], "textos": [],
                        "extra": extra, "inicio": ahora, "ultima": ahora}
                self._abiertos[telefono] = lote
                threading.Thread(target=self._esperar_y_procesar, args=(lote,), daemon=True).start()
            lote["urls"].extend(u for u in urls if u)
            if texto and texto.strip():
                lote["textos"].append(texto.strip())
            lote["ultima"] = ahora
        logger.info(f"[LOTE {lote['id']}] {telefono}: +{len(urls)} foto(s), van {len(lote['urls'])}")
        return nuevo

    def _esperar_y_procesar(self, lote):
        while True:
            with self._candado:
                ahora = time.monotonic()
                listo = (ahora - lote["ultima"] >= self.espera) or (ahora - lote["inicio"] >= self.espera_max)
                if listo:
                    # Cerrado: la próxima foto de este teléfono abre otro lote
                    if self._abiertos.get(lote["telefono"]) is lote:
                        del self._abiertos[lote["telefono"]]
                    break
            time.sleep(0.2)

        urls = lote["urls"]
        cerrado = {"id": lote["id"], "telefono": lote["telefono"], "urls": urls[:self.max_fotos],
                   "textos": lote["textos"], "extra": lote["extra"],
                   "sobrantes": max(0, len(urls) - self.max_fotos)}
        with self._candado:
            candado_tel = self._por_telefono.setdefault(lote["telefono"], threading.Lock())
        with candado_tel:
            logger.info(f"[LOTE {lote['id']}] cerrado con {len(urls)} foto(s); procesando {len(cerrado['urls'])}")
            try:
                self.procesar(cerrado)
            except Exception as e:
                logger.error(f"[LOTE {lote['id']}] falló: {e}", exc_info=True)
