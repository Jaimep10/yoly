"""
Reporte web editable por cliente: /reporte/{wa_id}?t={token}

API (todas filtran por wa_id y exigen el token firmado del link):
    GET    /api/gastos/{wa_id}?t=...            -> {"gastos": [...], "totales": {...}}
    PUT    /api/gastos/{id}?wa_id=...&t=...     -> edita monto y/o categoria
    DELETE /api/gastos/{id}?wa_id=...&t=...     -> borra el gasto

Los gastos viven en {DATA_DIR}/{telefono}/{cuenta}/gastos.json; la carpeta del teléfono se
busca por los últimos 10 dígitos (puede llamarse "whatsapp:+593..." o "593...").
Un cliente solo ve y toca los archivos de SU teléfono: el id se busca dentro de ellos.
"""
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import threading
import uuid

from flask import Blueprint, jsonify, render_template, request, send_file

from app.services import reporte_service

logger = logging.getLogger(__name__)

TEMPLATES = os.path.join(os.path.dirname(__file__), '..', '..', 'templates')
_candado = threading.RLock()


# ==================== TOKEN DEL LINK ====================

def _secreto():
    return os.getenv('YOLY_WEB_SECRET') or os.getenv('TWILIO_AUTH_TOKEN') or ''


def _ultimos10(wa_id):
    return re.sub(r'[^0-9]', '', wa_id or '')[-10:]


def token_para(wa_id):
    """Token del link personalizado de ese teléfono. None si falta el secreto o el teléfono."""
    secreto, tel = _secreto(), _ultimos10(wa_id)
    if not secreto or not tel:
        return None
    return hmac.new(secreto.encode(), f"reporte:{tel}".encode(), hashlib.sha256).hexdigest()[:24]


def token_valido(wa_id, token):
    esperado = token_para(wa_id)
    return bool(esperado and token and hmac.compare_digest(esperado, str(token)))


def link_reporte(server_url, wa_id):
    """Link que Yoly manda por WhatsApp: solo quien lo recibe puede editar sus gastos."""
    token = token_para(wa_id)
    if not token:
        return None
    return f"{server_url}/reporte/{_ultimos10(wa_id)}?t={token}"


# ==================== ARCHIVOS DEL CLIENTE ====================

def _leer(archivo):
    if not os.path.exists(archivo):
        return []
    try:
        with open(archivo, 'r', encoding='utf-8') as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except Exception as e:
        logger.warning(f"Error leyendo {archivo}: {e}")
        return []


def _escribir(archivo, datos):
    """Escribe a un temporal y lo cambia de golpe: nunca queda un JSON a medio escribir."""
    temporal = f"{archivo}.{os.getpid()}-{threading.get_ident()}.tmp"
    with open(temporal, 'w', encoding='utf-8') as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(temporal, archivo)


def _es_registro_pagos(gasto):
    """La foto de un registro de pagos (cobro de deuda) no es un gasto editable."""
    return isinstance(gasto.get('pagos'), list) and (len(gasto['pagos']) > 1 or gasto.get('cliente'))


def carpetas_cliente(data_dir, wa_id, cuenta="principal"):
    """Carpetas {data_dir}/{telefono}/{cuenta} cuyo teléfono termina en los mismos 10 dígitos."""
    tel = _ultimos10(wa_id)
    if not tel or not os.path.isdir(data_dir):
        return []
    rutas = []
    for c in sorted(os.listdir(data_dir)):
        ruta = os.path.join(data_dir, c, cuenta)
        if re.sub(r'[^0-9]', '', c)[-10:] == tel and os.path.isdir(ruta):
            rutas.append(ruta)
    return rutas


def _con_ids(archivo):
    """Gastos del archivo; a los que no tienen id (o lo repiten) les pone uno y guarda."""
    gastos, vistos, cambio = _leer(archivo), set(), False
    for g in gastos:
        if not isinstance(g, dict):
            continue
        if not g.get('id') or str(g['id']) in vistos:
            g['id'] = f"g_{uuid.uuid4().hex[:12]}"
            cambio = True
        vistos.add(str(g['id']))
    if cambio:
        _escribir(archivo, gastos)
    return gastos


def _a_numero(valor):
    try:
        return float(str(valor).replace('$', '').replace(',', '').strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _fila(g):
    return {
        "id": str(g.get('id')),
        "fecha": g.get('fecha') or '',
        "descripcion": g.get('descripcion') or g.get('proveedor') or g.get('cliente') or '',
        "monto": round(_a_numero(g.get('monto')), 2),
        "categoria": g.get('categoria') or 'otro',
    }


def listar(data_dir, wa_id, cuenta="principal"):
    """(gastos, ingresos) del cliente listos para la web, ordenados por fecha."""
    gastos, ingresos = [], []
    with _candado:
        for ruta in carpetas_cliente(data_dir, wa_id, cuenta):
            gastos += [_fila(g) for g in _con_ids(os.path.join(ruta, 'gastos.json'))
                       if isinstance(g, dict) and not _es_registro_pagos(g)]
            ingresos += [g for g in _leer(os.path.join(ruta, 'ingresos.json')) if isinstance(g, dict)]
    gastos.sort(key=lambda g: g['fecha'])
    return gastos, ingresos


def totales(gastos, ingresos):
    total_gastos = round(sum(g['monto'] for g in gastos), 2)
    total_ingresos = round(sum(_a_numero(i.get('monto')) for i in ingresos), 2)
    return {"ingresos": total_ingresos, "gastos": total_gastos,
            "saldo": round(total_ingresos - total_gastos, 2), "cantidad": len(gastos)}


def _cambiar(data_dir, wa_id, gasto_id, cuenta, cambio):
    """Aplica cambio(lista, i) al gasto con ese id SOLO dentro de los archivos de ese wa_id."""
    with _candado:
        for ruta in carpetas_cliente(data_dir, wa_id, cuenta):
            archivo = os.path.join(ruta, 'gastos.json')
            gastos = _leer(archivo)
            for i, g in enumerate(gastos):
                if isinstance(g, dict) and str(g.get('id')) == str(gasto_id) and not _es_registro_pagos(g):
                    resultado = cambio(gastos, i)
                    _escribir(archivo, gastos)
                    _cambiar_en_carpetas(ruta, gasto_id, resultado)
                    return resultado
    return None


def _cambiar_en_carpetas(ruta, gasto_id, resultado):
    """Las transferencias también viven en {cuenta}/{carpeta}/transacciones.json: mantenerlas iguales."""
    for sub in os.listdir(ruta):
        archivo = os.path.join(ruta, sub, 'transacciones.json')
        if not os.path.exists(archivo):
            continue
        datos = _leer(archivo)
        if resultado.get('borrado'):
            nuevos = [t for t in datos if str(t.get('id')) != str(gasto_id)]
        else:
            nuevos = [{**t, 'monto': resultado['monto']} if str(t.get('id')) == str(gasto_id) else t
                      for t in datos]
        if nuevos != datos:
            _escribir(archivo, nuevos)


def borrar(data_dir, wa_id, gasto_id, cuenta="principal"):
    def quitar(gastos, i):
        return {**_fila(gastos.pop(i)), 'borrado': True}
    return _cambiar(data_dir, wa_id, gasto_id, cuenta, quitar)


def editar(data_dir, wa_id, gasto_id, monto=None, categoria=None, cuenta="principal"):
    def actualizar(gastos, i):
        if monto is not None:
            gastos[i]['monto'] = monto
        if categoria is not None:
            gastos[i]['categoria'] = categoria
        gastos[i]['editado_web'] = True
        return _fila(gastos[i])
    return _cambiar(data_dir, wa_id, gasto_id, cuenta, actualizar)


# ==================== RUTAS ====================

def crear_blueprint(data_dir):
    """data_dir: función que devuelve la carpeta de datos (se lee en cada pedido)."""
    bp = Blueprint('reporte_web', __name__, template_folder=TEMPLATES)

    def _cuenta():
        return re.sub(r'[^\w-]', '', request.args.get('cuenta') or 'principal') or 'principal'

    def _no_autorizado():
        return jsonify({"error": "Link inválido o vencido. Pídele a Yoly un link nuevo."}), 403

    @bp.route("/reporte/<wa_id>", methods=["GET"])
    def pagina(wa_id):
        token = request.args.get('t', '')
        if not token_valido(wa_id, token):
            return render_template('reporte.html', invalido=True), 403
        return render_template('reporte.html', invalido=False, wa_id=_ultimos10(wa_id),
                               token=token, cuenta=_cuenta(), tel_corto=_ultimos10(wa_id)[-4:])

    @bp.route("/reporte/<wa_id>/excel", methods=["GET"])
    def excel(wa_id):
        if not token_valido(wa_id, request.args.get('t')):
            return _no_autorizado()
        gastos, ingresos = listar(data_dir(), wa_id, _cuenta())
        wb = reporte_service.generar_excel(_ultimos10(wa_id), gastos, ingresos, _cuenta())
        salida = io.BytesIO()
        wb.save(salida)
        salida.seek(0)
        return send_file(salida, as_attachment=True, download_name=f"yoly_reporte_{_ultimos10(wa_id)[-4:]}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @bp.route("/api/gastos/<wa_id>", methods=["GET"])
    def api_listar(wa_id):
        if not token_valido(wa_id, request.args.get('t')):
            return _no_autorizado()
        gastos, ingresos = listar(data_dir(), wa_id, _cuenta())
        return jsonify({"gastos": gastos, "totales": totales(gastos, ingresos)})

    @bp.route("/api/gastos/<gasto_id>", methods=["DELETE"])
    def api_borrar(gasto_id):
        wa_id = request.args.get('wa_id', '')
        if not token_valido(wa_id, request.args.get('t')):
            return _no_autorizado()
        borrado = borrar(data_dir(), wa_id, gasto_id, _cuenta())
        if not borrado:
            return jsonify({"error": "Ese gasto no existe"}), 404
        logger.info(f"[{_ultimos10(wa_id)}] Gasto borrado desde la web: {borrado}")
        gastos, ingresos = listar(data_dir(), wa_id, _cuenta())
        return jsonify({"ok": True, "gasto": borrado, "totales": totales(gastos, ingresos)})

    @bp.route("/api/gastos/<gasto_id>", methods=["PUT"])
    def api_editar(gasto_id):
        wa_id = request.args.get('wa_id', '')
        if not token_valido(wa_id, request.args.get('t')):
            return _no_autorizado()
        datos = request.get_json(silent=True) or {}
        monto = categoria = None
        if 'monto' in datos:
            monto = _a_numero(datos['monto'])
            if not math.isfinite(monto) or monto <= 0:
                return jsonify({"error": "El monto debe ser un número mayor que 0"}), 400
            monto = round(monto, 2)
        if 'categoria' in datos:
            categoria = str(datos['categoria']).strip()[:40]
            if not categoria:
                return jsonify({"error": "La categoría no puede quedar vacía"}), 400
        if monto is None and categoria is None:
            return jsonify({"error": "Manda monto o categoria"}), 400
        editado = editar(data_dir(), wa_id, gasto_id, monto, categoria, _cuenta())
        if not editado:
            return jsonify({"error": "Ese gasto no existe"}), 404
        logger.info(f"[{_ultimos10(wa_id)}] Gasto editado desde la web: {editado}")
        gastos, ingresos = listar(data_dir(), wa_id, _cuenta())
        return jsonify({"ok": True, "gasto": editado, "totales": totales(gastos, ingresos)})

    return bp
