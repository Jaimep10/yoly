# Pagos, gastos e ingresos escritos por WhatsApp: "pagué 50 de luz", "abono 100 a la deuda de Alan",
# "gasté 30 en comida ayer", "recibí 500 de sueldo". También las preguntas "¿cuánto he pagado de luz?"
# y "mis carpetas".
#
# Todo en Python (regex), sin IA: el monto y el concepto salen tal cual del mensaje, nunca se inventan.
# Cada movimiento se guarda en su carpeta (data/{tel}/{cuenta}/{carpeta}/transacciones.json) con su
# "rubro" (luz, deuda alan, comida...) y además en gastos.json / ingresos.json para los reportes por fecha.
import os
import re
from datetime import date, datetime, timedelta

import reportes

ORIGEN = 'texto'
CARPETA_GENERAL = reportes.CARPETA_GENERAL

_VERBOS_INGRESO = (r'me pagaron|me depositaron|me transfirieron|me dieron|me enviaron|me mandaron|recibi|recibimos|'
                   r'cobre|cobramos|gane|ganamos|vendi|vendimos|entraron|entro|ingresaron|ingreso')
_VERBOS_GASTO = (r'pague|pagamos|pago|pagado|gaste|gastamos|abone|abonamos|abono|compre|compramos|di|dimos|'
                 r'deposite|transferi|cancele|envie|mande|puse')
RE_INGRESO = re.compile(r'\b(' + _VERBOS_INGRESO + r')\b')
RE_GASTO = re.compile(r'\b(' + _VERBOS_GASTO + r')\b')

# Monto: 50 | $50 | 50.25 | 1,200 | 1.200 | 1,200.50 | 25,50
RE_MONTO = re.compile(r'(?<![\w/.,-])\$?\s?(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\w/]|[.,]\d)'
                      r'(?:\s*(?:dolares|dolar|usd|\$))?')
RE_FECHA_NUM = re.compile(r'\b(?:el\s+)?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b')
RE_FECHA_MES = re.compile(r'\b(?:el\s+)?(\d{1,2})\s+de\s+' + reportes._MES_RE + r'(?:\s+(?:de|del)\s+(\d{4}))?')
RE_METODO = re.compile(r'\b(?:en|con|por|via)\s+(efectivo|transferencia|tarjeta|cheque|deposito)\b|\b(efectivo)\b')
_RELLENO = {'de', 'del', 'en', 'a', 'al', 'la', 'el', 'los', 'las', 'por', 'para', 'mi', 'mis', 'un', 'una',
            'que', 'y', 'ya', 'le', 'lo', 'con', 'su', 'sus', 'nuestro', 'nuestra', 'total', 'como'}
_PERIODOS = re.compile(r'\b(este mes|mes pasado|el mes pasado|esta semana|semana pasada|la semana pasada|'
                       r'este ano|este año|en total|hasta ahora|hasta hoy|hoy|ayer|antier|anteayer)\b')
NO_REGISTRAR = re.compile(r'\b(meta|metas|ahorrar|presupuesto|quiero pagar|tengo que pagar|debo pagar|'
                          r'voy a pagar|hay que pagar|cuanto|cuando|como|donde|cual)\b')

RE_PREGUNTA = re.compile(r'\bcuanto\b.*?\b(pagad[oa]s?|pague|pagamos|abonad[oa]|abone|gastad[oa]|gaste|dad[oa]|'
                         r'di|recibid[oa]|recibi|cobrad[oa]|cobre|ganad[oa]|gane|entrad[oa]|entro)\b\s*(.*)$')
_VERBO_INGRESO_PREGUNTA = re.compile(r'^(recibid|recibi|cobrad|cobre|ganad|gane|entrad|entro)')
RE_CARPETAS = re.compile(r'\b(mis carpetas|mis rubros|mis deudas|por rubros?|por carpetas?|reporte de carpetas|'
                         r'resumen de carpetas|ver carpetas|carpetas|rubros|ingresos y (?:egresos|gastos))\b')
RE_DESHACER = re.compile(r'^(deshacer|deshaz|borra eso|borrar eso|me equivoque|'
                         r'borra(?:r)? (?:el |lo )?(?:ultimo|ultima)(?: (?:pago|gasto|ingreso|registro|movimiento))?)$')


def _norm(texto):
    return re.sub(r'\s+', ' ', reportes.sin_acentos(texto or '').lower()).strip()


def a_monto(texto):
    """'1,200.50' -> 1200.5, '25,50' -> 25.5, '1.200' -> 1200"""
    t = texto.replace(' ', '')
    if re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?', t):
        return float(t.replace(',', ''))
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?', t):
        return float(t.replace('.', '').replace(',', '.'))
    return float(t.replace(',', '.'))


def clave_rubro(texto):
    """'la Deuda de Alan' -> 'deuda alan'. Sirve para juntar pagos del mismo rubro aunque se escriban distinto."""
    palabras = re.findall(r'[a-zñ0-9]+', _norm(texto))
    return ' '.join(p for p in palabras if p not in _RELLENO)


def _limpiar_concepto(texto):
    palabras = re.findall(r"[a-zA-ZñÑáéíóúÁÉÍÓÚüÜ0-9]+", texto or '')
    while palabras and _norm(palabras[0]) in _RELLENO:
        palabras.pop(0)
    while palabras and _norm(palabras[-1]) in _RELLENO:
        palabras.pop()
    concepto = ' '.join(palabras)
    return concepto[:1].upper() + concepto[1:] if concepto else ''


def _fecha_texto(t, hoy):
    """(fecha, texto_sin_fecha). Entiende hoy, ayer, antier, 5/10, 5/10/2026 y '5 de octubre'."""
    fecha = None
    m = RE_FECHA_MES.search(t)
    if m:
        try:
            anio = int(m.group(3)) if m.group(3) else hoy.year
            fecha = date(anio, reportes.MESES[m.group(2)], int(m.group(1)))
            if not m.group(3) and fecha > hoy:
                fecha = fecha.replace(year=hoy.year - 1)
        except ValueError:
            fecha = None
        t = t[:m.start()] + ' ' + t[m.end():]
    m = RE_FECHA_NUM.search(t)
    if m and not fecha:
        try:
            anio = int(m.group(3)) if m.group(3) else hoy.year
            anio = anio + 2000 if anio < 100 else anio
            fecha = date(anio, int(m.group(2)), int(m.group(1)))
            if not m.group(3) and fecha > hoy:
                fecha = fecha.replace(year=hoy.year - 1)
        except ValueError:
            fecha = None
        t = t[:m.start()] + ' ' + t[m.end():]
    if re.search(r'\b(antier|anteayer)\b', t):
        fecha = fecha or hoy - timedelta(days=2)
    elif re.search(r'\bayer\b', t):
        fecha = fecha or hoy - timedelta(days=1)
    t = re.sub(r'\b(hoy|ayer|antier|anteayer)\b', ' ', t)
    return fecha or hoy, t


def parsear_registro(texto, hoy=None):
    """
    {"movimiento": gasto|ingreso, "monto", "concepto", "rubro", "fecha", "metodo"} o None.
    Solo si el mensaje dice claramente que pagó / gastó / recibió UN monto. Preguntas, metas,
    planes ("tengo que pagar") y mensajes con varios montos (desglose) no se registran aquí.
    """
    hoy = hoy or reportes.hoy_fecha()
    t = _norm(texto)
    if not t or '?' in t or '¿' in t or NO_REGISTRAR.search(t):
        return None
    if len(t.split()) > 25:
        return None
    ingreso = RE_INGRESO.search(t)
    gasto = RE_GASTO.search(t)
    verbo = ingreso if ingreso and (not gasto or ingreso.start() <= gasto.start()) else gasto
    if not verbo:
        return None
    movimiento = 'ingreso' if verbo is ingreso else 'gasto'

    fecha, resto = _fecha_texto(t, hoy)
    metodo = ''
    mm = RE_METODO.search(resto)
    if mm:
        metodo = mm.group(1) or mm.group(2)
        resto = resto[:mm.start()] + ' ' + resto[mm.end():]
    montos = list(RE_MONTO.finditer(resto))
    if len(montos) != 1:
        return None
    m = montos[0]
    monto = a_monto(m.group(1))
    if monto <= 0:
        return None

    # Concepto: lo que va después del monto ("pagué 50 de luz"); si no hay, lo de antes ("pagué la luz 50")
    v = (RE_INGRESO if movimiento == 'ingreso' else RE_GASTO).search(resto)
    despues = resto[m.end():]
    antes = resto[v.end():m.start()] if v and v.end() <= m.start() else resto[:m.start()]
    despues = RE_INGRESO.sub(' ', RE_GASTO.sub(' ', despues))
    antes = RE_INGRESO.sub(' ', RE_GASTO.sub(' ', antes))
    concepto = _limpiar_concepto(_conservar_mayusculas(texto, despues)) or _limpiar_concepto(_conservar_mayusculas(texto, antes))
    return {"movimiento": movimiento, "monto": round(monto, 2), "concepto": concepto,
            "rubro": clave_rubro(concepto), "fecha": fecha.isoformat(), "metodo": metodo}


def _conservar_mayusculas(original, fragmento_norm):
    """Devuelve el fragmento con las palabras como las escribió el usuario (Alan, no alan)."""
    originales = {_norm(p): p for p in re.findall(r"[a-zA-ZñÑáéíóúÁÉÍÓÚüÜ0-9]+", original or '')}
    return ' '.join(originales.get(p, p) for p in re.findall(r'[a-zñ0-9]+', fragmento_norm))


def parsear_pregunta(texto):
    """'¿cuánto he pagado de luz?' -> {"movimiento": "gasto", "rubro": "luz", "concepto": "Luz", "periodo"}"""
    t = _norm(texto).replace('¿', ' ').replace('?', ' ').strip()
    m = RE_PREGUNTA.search(t)
    if not m:
        return None
    movimiento = 'ingreso' if _VERBO_INGRESO_PREGUNTA.match(m.group(1)) else 'gasto'
    resto = m.group(2)
    periodo = reportes.parsear_periodo(resto) if resto else None
    if periodo:
        resto = re.sub(reportes._MES_RE, ' ', resto)
        resto = re.sub(r'\b(del?|al|hasta)\s+\d{1,2}\b|\b\d{1,4}\b', ' ', resto)
    resto = _PERIODOS.sub(' ', resto)
    concepto = _limpiar_concepto(_conservar_mayusculas(texto, resto))
    if not concepto:
        return None
    return {"movimiento": movimiento, "rubro": clave_rubro(concepto), "concepto": concepto, "periodo": periodo}


def pide_carpetas(texto):
    t = _norm(texto)
    return bool(RE_CARPETAS.search(t)) and len(t.split()) <= 10 and not re.search(r'\d', t)


def pide_deshacer(texto):
    return bool(RE_DESHACER.match(_norm(texto).strip(' .!')))


# ==================== GUARDAR ====================

def carpeta_para(registro):
    if registro['movimiento'] == 'ingreso':
        return reportes.CARPETA_INGRESO
    if not registro['concepto']:
        return reportes.CARPETA_REVISAR
    return reportes.clasificar_concepto(registro['concepto']) or CARPETA_GENERAL


def guardar_registro(data_dir, telefono, registro, escribir_json, cuenta="principal"):
    """Guarda el movimiento en su carpeta y en gastos.json / ingresos.json. Devuelve la transacción."""
    carpeta = carpeta_para(registro)
    ahora = datetime.now()
    transaccion = {
        "id": f"texto_{ahora.strftime('%Y%m%d_%H%M%S_%f')}",
        "fecha": registro['fecha'],
        "timestamp": ahora.isoformat(),
        "descripcion": registro['concepto'] or 'Sin concepto',
        "concepto": registro['concepto'],
        "rubro": registro['rubro'],
        "categoria": carpeta if carpeta not in (CARPETA_GENERAL, reportes.CARPETA_INGRESO) else (registro['rubro'] or carpeta),
        "movimiento": registro['movimiento'],
        "monto": registro['monto'],
        "metodo": registro.get('metodo', ''),
        "origen": ORIGEN,
        "revisar": carpeta == reportes.CARPETA_REVISAR,
    }
    reportes.guardar_en_carpeta(data_dir, telefono, carpeta, transaccion, cuenta)
    base = os.path.join(data_dir, telefono, cuenta)
    os.makedirs(base, exist_ok=True)
    archivo = os.path.join(base, 'ingresos.json' if registro['movimiento'] == 'ingreso' else 'gastos.json')
    lista = reportes._leer(archivo)
    lista.append(dict(transaccion, carpeta=carpeta))
    escribir_json(archivo, lista, ensure_ascii=False, indent=2)
    return dict(transaccion, carpeta=carpeta)


def deshacer_ultimo(data_dir, telefono, cuenta="principal"):
    """Borra el último movimiento escrito por texto (de su carpeta y de gastos/ingresos). Devuelve la transacción o None."""
    ultima = None
    for base in reportes._carpetas_usuario(data_dir, telefono, cuenta):
        for carpeta in reportes.TODAS_CARPETAS:
            for t in reportes._leer(os.path.join(base, carpeta, 'transacciones.json')):
                if t.get('origen') == ORIGEN and (ultima is None or (t.get('timestamp') or '') > (ultima[2].get('timestamp') or '')):
                    ultima = (base, carpeta, t)
    if not ultima:
        return None
    base, carpeta, t = ultima
    archivo = os.path.join(base, carpeta, 'transacciones.json')
    reportes._escribir(archivo, [x for x in reportes._leer(archivo) if x.get('id') != t.get('id')])
    for nombre in ('gastos.json', 'ingresos.json'):
        archivo = os.path.join(base, nombre)
        lista = reportes._leer(archivo)
        if any(x.get('id') == t.get('id') for x in lista):
            reportes._escribir(archivo, [x for x in lista if x.get('id') != t.get('id')])
    return t


# ==================== CONSULTAR ====================

def movimientos_usuario(data_dir, telefono, cuenta="principal"):
    """Todas las transacciones de todas las carpetas (texto, transferencias y facturas), con su carpeta."""
    todas = []
    for carpeta, lista in reportes.cargar_carpetas(data_dir, telefono, cuenta).items():
        for t in lista:
            todas.append(dict(t, carpeta=t.get('carpeta') or carpeta))
    todas.sort(key=lambda x: (x.get('fecha') or '', x.get('timestamp') or ''))
    return todas


def _palabras_de(t):
    return set(clave_rubro(' '.join(str(t.get(k) or '') for k in ('rubro', 'concepto', 'descripcion'))).split())


def coincide(t, rubro):
    """El movimiento es del rubro pedido: todas las palabras pedidas están en su rubro/concepto, o es su carpeta."""
    pedidas = rubro.split()
    if not pedidas:
        return False
    carpeta = t.get('carpeta') or ''
    nombres = {carpeta, reportes.sin_acentos(NOMBRES.get(carpeta, '')).lower()}
    if len(pedidas) == 1 and (pedidas[0] in nombres or pedidas[0].rstrip('s') in nombres
                              or pedidas[0] + 's' in nombres):
        return True
    palabras = _palabras_de(t)
    return all(p in palabras or p.rstrip('s') in palabras for p in pedidas)


def buscar(data_dir, telefono, pregunta, cuenta="principal"):
    movs = [t for t in movimientos_usuario(data_dir, telefono, cuenta)
            if (t.get('movimiento') or 'gasto') == pregunta['movimiento'] and coincide(t, pregunta['rubro'])]
    periodo = pregunta.get('periodo')
    if periodo:
        def en_periodo(t):
            try:
                f = datetime.strptime(t.get('fecha') or '', '%Y-%m-%d').date()
            except ValueError:
                return False
            return periodo['inicio'] <= f <= periodo['fin']
        movs = [t for t in movs if en_periodo(t)]
    return movs


NOMBRES = {"sueldo": "Sueldos", "renta": "Renta", "deuda": "Deudas", "servicios": "Servicios",
           "ingreso": "Ingresos", "compras": "Compras", "por_revisar": "Por revisar",
           CARPETA_GENERAL: "Gastos generales"}


def _fecha_corta(f):
    try:
        return datetime.strptime(f, '%Y-%m-%d').strftime('%d/%m/%Y')
    except (ValueError, TypeError):
        return 'sin fecha'


def total_rubro(data_dir, telefono, rubro, movimiento='gasto', cuenta="principal"):
    movs = buscar(data_dir, telefono, {"movimiento": movimiento, "rubro": rubro}, cuenta)
    return round(sum(reportes._a_numero(t.get('monto')) for t in movs), 2), len(movs)


def texto_registro(transaccion, total, cantidad):
    ingreso = transaccion['movimiento'] == 'ingreso'
    concepto = transaccion.get('concepto') or ''
    carpeta = transaccion['carpeta']
    lineas = [f"✅ Anotado: {'recibiste' if ingreso else 'pagaste'} {reportes.dinero(transaccion['monto'])}"
              + (f" de *{concepto}*" if concepto else "") + f" ({_fecha_corta(transaccion['fecha'])})."]
    if transaccion.get('metodo'):
        lineas.append(f"Método: {transaccion['metodo']}")
    lineas.append(f"{reportes.EMOJI_CARPETA.get(carpeta, '📁')} Carpeta: *{NOMBRES.get(carpeta, carpeta)}*")
    if carpeta == reportes.CARPETA_REVISAR:
        lineas.append("❓ No me dijiste de qué fue, la dejé en *Por revisar*. Escríbeme por ejemplo \"es renta\".")
    elif concepto and cantidad > 1:
        lineas.append(f"📊 Llevas {'recibido' if ingreso else 'pagado'} en {concepto}: "
                      f"*{reportes.dinero(total)}* ({cantidad} {'movimientos' if ingreso else 'pagos'}).")
    lineas.append("")
    lineas.append("Si me equivoqué, escribe \"borrar último\".")
    return "\n".join(lineas)


def texto_pregunta(movs, pregunta, server_url='', phone_clean=''):
    ingreso = pregunta['movimiento'] == 'ingreso'
    verbo = 'recibido' if ingreso else 'pagado'
    if not movs:
        ejemplo = f"recibí 100 de {pregunta['concepto'].lower()}" if ingreso else f"pagué 50 de {pregunta['concepto'].lower()}"
        return (f"No tengo nada {verbo} en *{pregunta['concepto']}* todavía.\n"
                f"Para anotarlo escríbeme por ejemplo: \"{ejemplo}\" o mándame la foto del recibo.")
    total = round(sum(reportes._a_numero(t.get('monto')) for t in movs), 2)
    lineas = [f"📁 *{pregunta['concepto']}*" + (f" ({pregunta['periodo']['etiqueta']})" if pregunta.get('periodo') else ""),
              f"Llevas {verbo}: *{reportes.dinero(total)}* en {len(movs)} {'movimientos' if ingreso else 'pagos'}", ""]
    for t in movs[-10:]:
        lineas.append(f"• {_fecha_corta(t.get('fecha'))}  {reportes.dinero(reportes._a_numero(t.get('monto')))}  {t.get('descripcion', '')}")
    if len(movs) > 10:
        lineas.append(f"(y {len(movs) - 10} más antes)")
    if server_url and phone_clean:
        lineas.append(f"\n📁 Todas tus carpetas: {server_url}/dashboard/{phone_clean}/carpetas")
    return "\n".join(lineas)


def resumen_rubros(movs):
    """{(movimiento, nombre_rubro): (total, cantidad)} agrupando por rubro; sin rubro, por carpeta."""
    acc = {}
    for t in movs:
        mov = t.get('movimiento') or 'gasto'
        clave = t.get('rubro') or t.get('carpeta') or 'otro'
        nombre = t.get('concepto') if t.get('rubro') else NOMBRES.get(clave, clave)
        actual = acc.setdefault((mov, clave), {"nombre": nombre or clave, "total": 0.0, "cantidad": 0})
        actual["total"] += reportes._a_numero(t.get('monto'))
        actual["cantidad"] += 1
    return acc


def texto_carpetas(movs, server_url='', phone_clean=''):
    if not movs:
        return ("Todavía no tienes movimientos guardados 📂\n"
                "Escríbeme por ejemplo \"pagué 50 de luz\", \"abono 100 a la deuda de Alan\" o "
                "\"recibí 500 de sueldo\", o mándame la foto de la factura.")
    ingresos = sum(reportes._a_numero(t.get('monto')) for t in movs if t.get('movimiento') == 'ingreso')
    gastos = sum(reportes._a_numero(t.get('monto')) for t in movs if t.get('movimiento') != 'ingreso')
    rubros = resumen_rubros(movs)
    lineas = ["📂 *Tus carpetas*", "",
              f"💰 Ingresos: {reportes.dinero(ingresos)}",
              f"📤 Egresos: {reportes.dinero(gastos)}",
              f"⚖️ Balance: {reportes.dinero(ingresos - gastos)}"]
    for mov, titulo in (('gasto', '📤 *Pagos por rubro / deuda*'), ('ingreso', '💰 *Ingresos por rubro*')):
        filas = sorted(((v['nombre'], v['total'], v['cantidad']) for (m, _), v in rubros.items() if m == mov),
                       key=lambda x: -x[1])
        if filas:
            lineas += ["", titulo]
            lineas += [f"• {n}: {reportes.dinero(round(tot, 2))} ({c})" for n, tot, c in filas[:15]]
    lineas.append("\nPregúntame \"¿cuánto he pagado de luz?\" para ver el detalle de un rubro.")
    if server_url and phone_clean:
        lineas.append(f"📁 Ver carpetas: {server_url}/dashboard/{phone_clean}/carpetas")
    return "\n".join(lineas)
