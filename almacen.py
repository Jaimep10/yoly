# Dónde guarda Yoly los datos de cada usuario (gastos, ingresos, carpetas, memoria).
#
# En Render el disco normal del servidor se BORRA en cada deploy o reinicio. Para que los datos
# queden, hay que agregar un Disco persistente en Render y montarlo en /var/data (o poner la
# variable DATA_DIR con la ruta del disco). Este módulo elige la carpeta y avisa si no es persistente.
import os

RAIZ_APP = os.path.dirname(os.path.abspath(__file__))
DISCO_RENDER = '/var/data'


def elegir_data_dir():
    """DATA_DIR de la variable de entorno; si no, el disco de Render (/var/data); si no, ./data del proyecto."""
    ruta = (os.environ.get('DATA_DIR') or os.environ.get('YOLY_DATA_DIR') or '').strip()
    if ruta:
        return ruta
    if os.path.isdir(DISCO_RENDER) and os.access(DISCO_RENDER, os.W_OK):
        return DISCO_RENDER
    return os.path.join(RAIZ_APP, 'data')


def _punto_de_montaje(ruta):
    ruta = os.path.realpath(ruta)
    while not os.path.ismount(ruta):
        ruta = os.path.dirname(ruta)
    return ruta


def revisar(data_dir):
    """{data_dir, escribible, persistente, error}. Persistente = está en un disco montado aparte (no en '/')."""
    estado = {"data_dir": data_dir, "escribible": False, "persistente": False, "error": ""}
    try:
        os.makedirs(data_dir, exist_ok=True)
        prueba = os.path.join(data_dir, '.prueba_escritura')
        with open(prueba, 'w', encoding='utf-8') as f:
            f.write('ok')
        os.remove(prueba)
        estado["escribible"] = True
    except Exception as e:
        estado["error"] = str(e)
        return estado
    forzado = (os.environ.get('YOLY_DISCO_PERSISTENTE') or '').strip().lower()
    if forzado in ('1', 'si', 'sí', 'true', 'yes'):
        estado["persistente"] = True
    elif not os.environ.get('RENDER'):
        # Fuera de Render (tu computadora) los archivos no se borran solos
        estado["persistente"] = True
    else:
        estado["persistente"] = _punto_de_montaje(data_dir) != '/'
    return estado
