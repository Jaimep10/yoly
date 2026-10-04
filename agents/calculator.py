# Agente 3 - "La Calculadora": Python puro, sin Vision ni IA.
# Recibe el JSON que leyó el Ojo (agents/vision.py) y saca las cuentas:
# pagado = sum(montos), saldo = deuda - pagado, saldo restante pago por pago,
# y un hash para no guardar dos veces el mismo registro.
import hashlib
import re
from datetime import datetime


def a_numero(valor):
    """Convierte '1.200', '$300' o 300 a float; None si no es número"""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    if isinstance(valor, str):
        limpio = re.sub(r'[^0-9.,-]', '', valor).replace(',', '')
        try:
            return float(limpio)
        except ValueError:
            return None
    return None


def fecha_valida(fecha):
    try:
        datetime.strptime(str(fecha), '%Y-%m-%d')
        return True
    except ValueError:
        return False


MESES = {
    'enero': 1, 'ene': 1, 'january': 1, 'jan': 1,
    'febrero': 2, 'feb': 2, 'february': 2,
    'marzo': 3, 'mar': 3, 'march': 3,
    'abril': 4, 'abr': 4, 'april': 4, 'apr': 4,
    'mayo': 5, 'may': 5,
    'junio': 6, 'jun': 6, 'june': 6,
    'julio': 7, 'jul': 7, 'july': 7,
    'agosto': 8, 'ago': 8, 'august': 8, 'aug': 8,
    'septiembre': 9, 'setiembre': 9, 'sep': 9, 'sept': 9, 'set': 9, 'september': 9,
    'octubre': 10, 'oct': 10, 'october': 10,
    'noviembre': 11, 'nov': 11, 'november': 11,
    'diciembre': 12, 'dic': 12, 'december': 12, 'dec': 12,
}


def _anio(texto):
    anio = int(texto)
    return anio + 2000 if anio < 100 else anio


def _armar(anio, mes, dia):
    try:
        return datetime(anio, mes, dia)
    except ValueError:
        return None


def _no_futura(fecha, hoy):
    """Un ticket no puede ser de mañana en adelante (1 día de margen por zona horaria)."""
    return fecha is not None and (fecha - hoy).days <= 1


def normalizar_fecha(valor, hoy=None):
    """
    Fecha impresa en una factura -> 'YYYY-MM-DD', o '' si no se entiende (no se inventa).
    Acepta '2026-10-04', '04/10/2026', '04-10-2026', '04.10.2026', '04/10/26', '4 de octubre 2026',
    'October 4, 2026', 'Oct 4, 26', '04-Oct-2026', con o sin hora al final.
    Números ambiguos (04/10) se leen día/mes; si así queda en el futuro y mes/día no, se usa mes/día
    (tickets de USA). Si el día pasa de 12 no hay duda.
    """
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        return valor.strftime('%Y-%m-%d')
    texto = str(valor).strip().lower()
    if not texto:
        return ""
    hoy = hoy or datetime.now()

    # Año primero: 2026-10-04, 2026/10/04, 2026.10.04
    m = re.search(r'\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b', texto)
    if m:
        anio, mes, dia = int(m.group(1)), int(m.group(2)), int(m.group(3))
        fecha = _armar(anio, mes, dia)
        if fecha and not _no_futura(fecha, hoy) and dia <= 12:
            # Vision a veces cambia día y mes; si al revés queda en el pasado, era al revés
            al_reves = _armar(anio, dia, mes)
            if _no_futura(al_reves, hoy):
                fecha = al_reves
        return fecha.strftime('%Y-%m-%d') if fecha else ""

    # Solo números: 04/10/2026, 04-10-2026, 04.10.26
    m = re.search(r'\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})\b', texto)
    if m:
        a, b, anio = int(m.group(1)), int(m.group(2)), _anio(m.group(3))
        dia_mes, mes_dia = _armar(anio, b, a), _armar(anio, a, b)
        if a > 12:
            fecha = dia_mes
        elif b > 12:
            fecha = mes_dia
        elif not _no_futura(dia_mes, hoy) and _no_futura(mes_dia, hoy):
            fecha = mes_dia
        else:
            fecha = dia_mes
        return fecha.strftime('%Y-%m-%d') if fecha else ""

    # Con el mes en letras
    palabras = re.findall(r'[a-záéíóúñ]+|\d+', texto)
    mes = next((MESES[p.rstrip('.')] for p in palabras if p.rstrip('.') in MESES), None)
    numeros = [p for p in palabras if p.isdigit()]
    if mes and numeros:
        anio = next((int(n) for n in numeros if len(n) == 4), None)
        resto = [int(n) for n in numeros if len(n) != 4]
        dia = next((n for n in resto if 1 <= n <= 31), None)
        if dia is None:
            return ""
        if anio is None:
            # "Oct 4, 26": el número que no es el día es el año
            otros = [n for n in resto if n != dia] or resto[1:]
            anio = _anio(otros[0]) if otros else hoy.year
        fecha = _armar(anio, mes, dia)
        return fecha.strftime('%Y-%m-%d') if fecha else ""
    return ""


METODOS_PAGO = [
    ('transferencia', ['transferencia', 'transf', 'trans', 't']),
    ('efectivo', ['efectivo', 'efec', 'efect', 'e']),
    ('cheque', ['cheque', 'chq', 'ch']),
    ('deposito', ['deposito', 'depósito', 'dep']),
    ('zelle', ['zelle']),
]


def normalizar_metodo(metodo):
    """'T', 'transf', 'Efec' -> 'transferencia', 'efectivo'; vacío -> 'no especificado'"""
    texto = str(metodo or '').strip().lower().rstrip('.')
    for nombre, claves in METODOS_PAGO:
        if texto in claves or any(texto.startswith(c) for c in claves if len(c) > 2):
            return nombre
    return texto if texto and texto not in ('null', 'none', 'n/a') else 'no especificado'


def normalizar_pagos(pagos_raw):
    """Acepta [200, 120] o [{"fecha", "monto", "metodo", "nota"}] y devuelve [{"fecha", "monto", "metodo", "nota"}]"""
    pagos = []
    for p in pagos_raw if isinstance(pagos_raw, list) else []:
        if isinstance(p, dict):
            monto = a_numero(p.get('monto'))
            fecha, metodo, nota = p.get('fecha'), p.get('metodo'), p.get('nota')
        else:
            monto, fecha, metodo, nota = a_numero(p), None, None, None
        if monto is None or monto <= 0:
            continue
        pagos.append({
            "fecha": normalizar_fecha(fecha),
            "monto": monto,
            "metodo": normalizar_metodo(metodo),
            "nota": str(nota or '').strip(),
        })
    return pagos


def fecha_corta(fecha):
    """'2026-01-03' -> '03-01-26'"""
    return datetime.strptime(fecha, '%Y-%m-%d').strftime('%d-%m-%y') if fecha else 'sin fecha'


def armar_cobro(datos):
    """Recalcula en Python pagado, saldo, saldo restante por pago y frecuencia"""
    pagos = normalizar_pagos(datos.get('pagos', []))
    deuda = a_numero(datos.get('deuda')) or 0
    pagado = sum(p['monto'] for p in pagos)

    saldo_restante = deuda
    filas = []
    for p in pagos:
        saldo_restante -= p['monto']
        filas.append({"fecha": p['fecha'], "fecha_corta": fecha_corta(p['fecha']), "monto": p['monto'],
                      "metodo": p['metodo'], "nota": p['nota'], "saldo": saldo_restante})

    fechas = sorted(datetime.strptime(p['fecha'], '%Y-%m-%d') for p in pagos if p['fecha'])
    frecuencia = None
    if len(fechas) >= 2:
        frecuencia = round((fechas[-1] - fechas[0]).days / (len(fechas) - 1))

    return {
        "tipo": "cobro_deuda",
        "cliente": datos.get('cliente') or 'Cliente',
        "deuda": deuda,
        "pagado": pagado,
        "saldo": deuda - pagado,
        "pagos": pagos,
        "filas": filas,
        "frecuencia_dias": frecuencia,
        "fecha": datos.get('fecha', ''),
    }


def hash_cobro(cobro):
    """Huella del registro: cliente + deuda + primera y última fecha (+ cuántos pagos y cuánto suman,
    para que una libretita con un pago nuevo sin fecha no se confunda con la anterior)."""
    pagos = normalizar_pagos(cobro.get('pagos', []))
    fechas = sorted(p['fecha'] for p in pagos if p['fecha'])
    partes = [
        str(cobro.get('cliente') or 'Cliente').strip().lower(),
        f"{a_numero(cobro.get('deuda')) or 0:.2f}",
        fechas[0] if fechas else "",
        fechas[-1] if fechas else "",
        str(len(pagos)),
        f"{sum(p['monto'] for p in pagos):.2f}",
    ]
    return hashlib.sha256("|".join(partes).encode('utf-8')).hexdigest()[:16]


def combinar(lista_vision):
    """Junta varias fotos de la misma libretita (página 1, página 2...) en un solo registro.
    Pagos en orden de las fotos; un pago con la misma fecha, monto y método que ya apareció
    (foto que se solapa con la anterior) no se cuenta dos veces."""
    lista = [d for d in lista_vision if isinstance(d, dict)]
    if len(lista) == 1:
        return lista[0]
    combinado = {"cliente": None, "deuda": 0, "pagos": [], "descripcion": None}
    vistos = set()
    for datos in lista:
        cliente = datos.get('cliente')
        if cliente and cliente != 'Cliente' and not combinado['cliente']:
            combinado['cliente'] = cliente
        deuda = a_numero(datos.get('deuda')) or 0
        if deuda > 0 and not combinado['deuda']:
            combinado['deuda'] = deuda
        combinado['descripcion'] = combinado['descripcion'] or datos.get('descripcion')
        for p in normalizar_pagos(datos.get('pagos', [])):
            clave = (p['fecha'], p['monto'], p['metodo'])
            if p['fecha'] and clave in vistos:
                continue
            vistos.add(clave)
            combinado['pagos'].append(p)
    return combinado


def calcular_totales(datos_vision, existente=None):
    """
    Entrada: JSON del Ojo {"cliente", "deuda", "pagos": [...]} (o una lista de varios, se combinan).
    existente: el cobro que ya está guardado para este usuario (memoria_global.json), si hay.
    Salida: el cobro con pagado = sum(), saldo = deuda - pagado, filas con saldo restante,
    hash, valido (hay al menos un pago) y duplicado (mismo hash que lo ya guardado).
    """
    if isinstance(datos_vision, list):
        datos_vision = combinar(datos_vision)
    datos_vision = datos_vision or {}
    cobro = armar_cobro(datos_vision)
    cobro['descripcion'] = datos_vision.get('descripcion') or ''
    cobro['hash'] = hash_cobro(cobro)
    cobro['valido'] = len(cobro['pagos']) > 0
    cobro['duplicado'] = bool(existente) and (existente.get('hash') or hash_cobro(existente)) == cobro['hash']
    return cobro


def total_documento(datos_vision):
    """Factura o transferencia: pagos normalizados y su suma (en Python, nunca la de Vision)."""
    pagos = normalizar_pagos((datos_vision or {}).get('pagos', []))
    return pagos, sum(p['monto'] for p in pagos)


class Calculadora:
    """Agente 3: sumas determinísticas en Python, saldo y duplicados por hash."""

    def calcular(self, extracciones, tipo=None, existente=None):
        """Varias fotos de libretita -> un solo cobro con pagado, saldo, hash y duplicado."""
        return calcular_totales(list(extracciones), existente)

    def total_documento(self, extraccion):
        """Factura o transferencia -> (pagos, total)."""
        return total_documento(extraccion)
