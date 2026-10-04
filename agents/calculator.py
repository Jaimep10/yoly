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
            "fecha": fecha if fecha and fecha_valida(fecha) else "",
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
