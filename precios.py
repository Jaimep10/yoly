# Comparador de precios entre facturas, alimentado por los usuarios y separado por ciudad.
#
# Datos (base = DATA_DIR de main.py):
#   {base}/precios/{ciudad}/articulos.json   muestras anónimas de precios de esa ciudad
#   {base}/{telefono}/perfil.json            ciudad del usuario
#   {base}/{telefono}/precios_pendientes.json  productos leídos antes de saber su ciudad
#
# Solo se comparan productos de la MISMA ciudad y de la misma medida (2kg con 2kg).
import hashlib
import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timedelta

_candado = threading.Lock()

DIAS_VIGENCIA = 180  # precios más viejos no cuentan para comparar

CIUDADES_CONOCIDAS = {
    'quito', 'guayaquil', 'cuenca', 'ambato', 'manta', 'machala', 'loja', 'riobamba', 'santo domingo',
    'portoviejo', 'ibarra', 'esmeraldas', 'latacunga', 'quevedo', 'babahoyo', 'milagro', 'tulcan',
    'duran', 'salinas', 'otavalo', 'azogues', 'puyo', 'tena', 'sangolqui', 'cayambe', 'daule',
    'samborondon', 'la libertad', 'santa elena', 'nueva york', 'new york', 'queens', 'brooklyn',
    'bronx', 'newark', 'new jersey', 'chicago', 'miami', 'los angeles', 'madrid', 'barcelona',
    'murcia', 'valencia',
}
ALIAS_CIUDAD = {'uio': 'quito', 'gye': 'guayaquil', 'nyc': 'nueva york', 'new york': 'nueva york',
                'ny': 'nueva york', 'santo domingo de los tsachilas': 'santo domingo'}
CIUDAD_VACIA = {'', 'no especificada', 'no especificado', 'desconocida', 'desconocido', 'n/a', 'na',
                'null', 'none', 'ninguna'}

PALABRAS_VACIAS = {'de', 'del', 'la', 'el', 'los', 'las', 'en', 'con', 'sin', 'para', 'x', 'y', 'a', 'al',
                   'un', 'una', 'pack', 'paquete', 'funda', 'unidad', 'unidades', 'und', 'unid', 'u',
                   'kg', 'g', 'gr', 'grs', 'lt', 'l', 'ml', 'lb', 'cc', 'oz', 'el', 'mas', 'barato',
                   'barata'}

_UNIDADES = {
    'kg': ('g', 1000), 'kgs': ('g', 1000), 'kilo': ('g', 1000), 'kilos': ('g', 1000),
    'g': ('g', 1), 'gr': ('g', 1), 'grs': ('g', 1), 'gramos': ('g', 1),
    'lb': ('g', 453.592), 'lbs': ('g', 453.592), 'libra': ('g', 453.592), 'libras': ('g', 453.592),
    'oz': ('g', 28.3495),
    'l': ('ml', 1000), 'lt': ('ml', 1000), 'lts': ('ml', 1000), 'litro': ('ml', 1000), 'litros': ('ml', 1000),
    'ml': ('ml', 1), 'cc': ('ml', 1),
    'u': ('u', 1), 'und': ('u', 1), 'unid': ('u', 1), 'unidades': ('u', 1),
}
_RE_MEDIDA = re.compile(r'(\d+(?:[.,]\d+)?)\s*(' + '|'.join(sorted(_UNIDADES, key=len, reverse=True)) + r')\b')


# ==================== NORMALIZACIÓN ====================

def sin_acentos(texto):
    texto = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn')


def a_precio(valor):
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return round(float(valor), 2) if valor > 0 else None
    if isinstance(valor, str):
        limpio = re.sub(r'[^0-9.,]', '', valor).replace(',', '.')
        try:
            numero = float(limpio)
        except ValueError:
            return None
        return round(numero, 2) if numero > 0 else None
    return None


def normalizar_ciudad(texto):
    """'Quito, Pichincha' -> ('quito', 'Quito'). Devuelve (None, None) si no hay ciudad."""
    if not texto or not isinstance(texto, str):
        return None, None
    parte = re.split(r',|\s-\s|\(', texto)[0].strip()
    parte = re.sub(r'\s+ecuador$', '', parte, flags=re.I).strip(' .¿?!')
    clave = re.sub(r'\s+', ' ', sin_acentos(parte).lower()).strip()
    if clave in CIUDAD_VACIA or len(clave) < 2:
        return None, None
    clave = ALIAS_CIUDAD.get(clave, clave)
    slug = re.sub(r'[^a-z0-9]+', '_', clave).strip('_')
    if not slug:
        return None, None
    nombre = parte.title() if sin_acentos(parte).lower() == clave else clave.title()
    return slug, nombre


def parsear_medida(texto):
    """'2 kg' -> (2000.0, 'g'); '500ml' -> (500.0, 'ml'). None si no hay medida."""
    m = _RE_MEDIDA.search(sin_acentos(texto or '').lower())
    if not m:
        return None
    familia, factor = _UNIDADES[m.group(2)]
    return round(float(m.group(1).replace(',', '.')) * factor, 3), familia


def medida_texto(medida):
    if not medida:
        return ''
    cantidad, familia = medida
    if familia == 'g':
        return f"{cantidad / 1000:g}kg" if cantidad >= 1000 else f"{cantidad:g}g"
    if familia == 'ml':
        return f"{cantidad / 1000:g}L" if cantidad >= 1000 else f"{cantidad:g}ml"
    return f"{cantidad:g} u"


def precio_unitario_texto(precio, medida):
    """$/kg, $/L o $/u, para comparar tamaños distintos."""
    if not medida or not medida[0]:
        return ''
    cantidad, familia = medida
    if familia == 'g':
        return f"${precio / cantidad * 1000:.2f}/kg"
    if familia == 'ml':
        return f"${precio / cantidad * 1000:.2f}/L"
    return f"${precio / cantidad:.2f}/u" if cantidad > 1 else ''


def _singular(palabra):
    if len(palabra) > 4 and palabra.endswith('es') and palabra[-3] in 'lnrdzj':
        return palabra[:-2]
    if len(palabra) > 3 and palabra.endswith('s'):
        return palabra[:-1]
    return palabra


def tokens(texto):
    texto = _RE_MEDIDA.sub(' ', sin_acentos(texto or '').lower())
    palabras = re.findall(r'[a-z0-9ñ]+', texto)
    return {_singular(p) for p in palabras if p not in PALABRAS_VACIAS and not p.isdigit()}


def clave_producto(muestra):
    """Clave para agrupar el mismo producto y medida: 'arroz blanco|2kg'."""
    base = ' '.join(sorted(tokens(muestra.get('producto_norm') or muestra.get('producto'))))
    return f"{base}|{muestra.get('medida_norm', '')}"


def mismo_producto(a, b):
    """Mismo producto y misma medida (la marca puede cambiar: es la alternativa más barata)."""
    ta = tokens(a.get('producto_norm') or a.get('producto'))
    tb = tokens(b.get('producto_norm') or b.get('producto'))
    if not ta or not tb:
        return False
    if not (ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.6):
        return False
    return a.get('medida_norm', '') == b.get('medida_norm', '')


def coincide_busqueda(consulta_tokens, consulta_medida, muestra):
    texto = ' '.join(str(muestra.get(k) or '') for k in ('producto_norm', 'producto', 'marca'))
    if not consulta_tokens or not consulta_tokens <= tokens(texto):
        return False
    return not consulta_medida or muestra.get('medida_norm') == consulta_medida


def limpiar_articulo(art):
    """Valida un artículo leído por Vision. None si no tiene producto o precio claro."""
    if not isinstance(art, dict):
        return None
    producto = str(art.get('producto') or '').strip()
    precio = a_precio(art.get('precio'))
    if not producto or not precio:
        return None
    medida_raw = str(art.get('medida') or '')
    medida = parsear_medida(medida_raw) or parsear_medida(producto)
    marca = str(art.get('marca') or '').strip()
    if sin_acentos(marca).lower() in ('desconocido', 'desconocida', 'n/a', 'null', 'none'):
        marca = ''
    norm = sin_acentos(str(art.get('producto_norm') or producto)).lower().strip()
    return {
        'producto': producto,
        'producto_norm': norm,
        'marca': marca,
        'medida': medida_raw or medida_texto(medida),
        'medida_norm': medida_texto(medida),
        'categoria': str(art.get('categoria') or '').strip(),
        'precio': precio,
    }


# ==================== ARCHIVOS ====================

def _leer(ruta, defecto):
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def _escribir(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ruta)


def ruta_ciudad(base, slug):
    return os.path.join(base, 'precios', slug, 'articulos.json')


def ciudades_con_datos(base):
    carpeta = os.path.join(base, 'precios')
    if not os.path.isdir(carpeta):
        return []
    return sorted(d for d in os.listdir(carpeta) if os.path.exists(ruta_ciudad(base, d)))


def cargar_ciudad(base, slug, vigentes=True):
    datos = _leer(ruta_ciudad(base, slug), {})
    muestras = datos.get('articulos', []) if isinstance(datos, dict) else []
    if vigentes:
        limite = (datetime.now() - timedelta(days=DIAS_VIGENCIA)).strftime('%Y-%m-%d')
        muestras = [m for m in muestras if (m.get('fecha') or '') >= limite]
    return muestras


def nombre_ciudad(base, slug):
    datos = _leer(ruta_ciudad(base, slug), {})
    return (datos.get('ciudad') if isinstance(datos, dict) else None) or slug.replace('_', ' ').title()


def fuente_anonima(telefono):
    """Identifica al usuario sin guardar su número (para no contar dos veces la misma factura)."""
    return hashlib.sha256(re.sub(r'\D', '', telefono or '').encode()).hexdigest()[:12]


def agregar_muestras(base, ciudad, tienda, articulos, telefono, fecha_compra=None):
    """Suma los precios de una factura al histórico anónimo de la ciudad. Devuelve cuántos agregó."""
    slug, nombre = normalizar_ciudad(ciudad)
    if not slug:
        return 0
    fecha = fecha_compra if _fecha_ok(fecha_compra) else datetime.now().strftime('%Y-%m-%d')
    fuente = fuente_anonima(telefono)
    tienda = (tienda or 'Desconocida').strip()
    with _candado:
        ruta = ruta_ciudad(base, slug)
        datos = _leer(ruta, {})
        if not isinstance(datos, dict):
            datos = {}
        muestras = datos.get('articulos', [])
        vistas = {(m.get('fuente'), m.get('tienda'), m.get('producto_norm'), m.get('precio'), m.get('fecha'))
                  for m in muestras}
        agregados = 0
        for art in articulos:
            llave = (fuente, tienda, art['producto_norm'], art['precio'], fecha)
            if llave in vistas:
                continue  # la misma factura enviada dos veces
            vistas.add(llave)
            muestras.append({**art, 'tienda': tienda, 'fecha': fecha, 'fuente': fuente,
                             'registrado': datetime.now().isoformat(timespec='seconds')})
            agregados += 1
        _escribir(ruta, {'ciudad': datos.get('ciudad') or nombre, 'articulos': muestras})
    return agregados


def _fecha_ok(fecha):
    try:
        datetime.strptime(str(fecha), '%Y-%m-%d')
        return True
    except ValueError:
        return False


def ciudad_usuario(base, phone):
    perfil = _leer(os.path.join(base, phone, 'perfil.json'), {})
    return perfil.get('ciudad') if isinstance(perfil, dict) else None


def guardar_ciudad_usuario(base, phone, ciudad):
    slug, nombre = normalizar_ciudad(ciudad)
    if not slug:
        return None
    with _candado:
        ruta = os.path.join(base, phone, 'perfil.json')
        perfil = _leer(ruta, {})
        perfil = perfil if isinstance(perfil, dict) else {}
        perfil['ciudad'] = nombre
        _escribir(ruta, perfil)
    return nombre


def guardar_pendientes(base, phone, tienda, articulos, fecha_compra):
    with _candado:
        ruta = os.path.join(base, phone, 'precios_pendientes.json')
        pendientes = _leer(ruta, [])
        pendientes = pendientes if isinstance(pendientes, list) else []
        pendientes.append({'tienda': tienda, 'fecha': fecha_compra, 'articulos': articulos})
        _escribir(ruta, pendientes)


def tomar_pendientes(base, phone):
    with _candado:
        ruta = os.path.join(base, phone, 'precios_pendientes.json')
        pendientes = _leer(ruta, [])
        if os.path.exists(ruta):
            os.remove(ruta)
    return pendientes if isinstance(pendientes, list) else []


# ==================== COMPARACIONES ====================

def _por_tienda(muestras):
    """Último precio y promedio por tienda (y medida). Ordena del más barato al más caro."""
    grupos = {}
    for m in muestras:
        llave = (m.get('tienda', ''), m.get('medida_norm', ''))
        grupos.setdefault(llave, []).append(m)
    filas = []
    for (tienda, medida), items in grupos.items():
        items.sort(key=lambda m: (m.get('fecha', ''), m.get('registrado', '')))
        ultimo = items[-1]
        precios = [m['precio'] for m in items]
        filas.append({
            'tienda': tienda,
            'producto': ultimo.get('producto', ''),
            'marca': ultimo.get('marca', ''),
            'medida': medida,
            'precio': ultimo['precio'],
            'precio_prom': round(sum(precios) / len(precios), 2),
            'muestras': len(items),
            'fecha': ultimo.get('fecha', ''),
        })
    medidas = [parsear_medida(f['medida']) for f in filas]
    familias = {m[1] for m in medidas if m}
    if all(medidas) and len(familias) == 1:
        for fila, med in zip(filas, medidas):
            fila['unitario'] = fila['precio'] / med[0]
            fila['precio_unitario'] = precio_unitario_texto(fila['precio'], med)
        filas.sort(key=lambda f: f['unitario'])
    else:
        filas.sort(key=lambda f: f['precio'])
    return filas


def buscar_en_ciudad(base, ciudad, consulta):
    slug, _ = normalizar_ciudad(ciudad)
    if not slug:
        return []
    ct, cm = tokens(consulta), medida_texto(parsear_medida(consulta))
    return _por_tienda([m for m in cargar_ciudad(base, slug) if coincide_busqueda(ct, cm, m)])


def buscar_entre_ciudades(base, consulta):
    ct, cm = tokens(consulta), medida_texto(parsear_medida(consulta))
    filas = []
    for slug in ciudades_con_datos(base):
        tiendas = _por_tienda([m for m in cargar_ciudad(base, slug) if coincide_busqueda(ct, cm, m)])
        if tiendas:
            precios = [t['precio_prom'] for t in tiendas]
            filas.append({'ciudad': nombre_ciudad(base, slug), 'mas_barato': tiendas[0],
                          'precio_prom': round(sum(precios) / len(precios), 2),
                          'tiendas': len(tiendas)})
    filas.sort(key=lambda f: f['mas_barato'].get('unitario', f['mas_barato']['precio']))
    return filas


def comparativas_ciudad(base, ciudad):
    """Por cada producto+medida con precio en 2 o más tiendas: tiendas de más barata a más cara."""
    slug, _ = normalizar_ciudad(ciudad)
    if not slug:
        return []
    grupos = {}
    for m in cargar_ciudad(base, slug):
        grupos.setdefault(clave_producto(m), []).append(m)
    resultado = []
    for clave, muestras in grupos.items():
        tiendas = _por_tienda(muestras)
        if len(tiendas) < 2:
            continue
        resultado.append({'clave': clave, 'producto': tiendas[0]['producto'], 'medida': tiendas[0]['medida'],
                          'tiendas': tiendas, 'ahorro': round(tiendas[-1]['precio'] - tiendas[0]['precio'], 2)})
    resultado.sort(key=lambda r: (-len(r['tiendas']), -r['ahorro']))
    return resultado


def ranking_tiendas(base, ciudad):
    """Qué tienda sale más barata en promedio: compara cada precio contra el promedio del producto."""
    indices = {}
    for comp in comparativas_ciudad(base, ciudad):
        promedio = sum(t['precio'] for t in comp['tiendas']) / len(comp['tiendas'])
        for t in comp['tiendas']:
            indices.setdefault(t['tienda'], []).append(t['precio'] / promedio)
    filas = [{'tienda': tienda, 'indice': sum(v) / len(v), 'productos': len(v)} for tienda, v in indices.items()]
    filas.sort(key=lambda f: f['indice'])
    return filas


def comparar_factura(base, ciudad, tienda, articulos):
    """Para cada artículo comprado, busca otra tienda de la misma ciudad con el mismo producto más barato."""
    slug, _ = normalizar_ciudad(ciudad)
    if not slug:
        return []
    historico = cargar_ciudad(base, slug)
    ahorros = []
    for art in articulos:
        otras = [m for m in historico if m.get('tienda') != tienda and mismo_producto(art, m)]
        if not otras:
            continue
        mejor = _por_tienda(otras)[0]
        if mejor['precio'] < art['precio']:
            ahorros.append({'articulo': art, 'tienda': mejor['tienda'], 'precio': mejor['precio'],
                            'marca': mejor['marca'], 'ahorro': round(art['precio'] - mejor['precio'], 2)})
    ahorros.sort(key=lambda a: -a['ahorro'])
    return ahorros


# ==================== MENSAJES DE YOLY ====================

def _nombre_corto(fila):
    marca = f" {fila['marca']}" if fila.get('marca') and fila['marca'].lower() not in fila['producto'].lower() else ''
    medida = f" {fila['medida']}" if fila.get('medida') and fila['medida'].lower() not in fila['producto'].lower() else ''
    return f"{fila['producto']}{marca}{medida}"


def msg_buscar(consulta, ciudad, filas):
    if not filas:
        return (f"Todavía no tengo precios de *{consulta}* en {ciudad}. 📸 Mándame la foto de tu factura "
                f"de compra y empiezo a comparar.")
    tiendas = len({f['tienda'] for f in filas})
    texto = f"🛒 Encontré *{consulta}* en {tiendas} tienda{'s' if tiendas != 1 else ''} de {ciudad}:\n\n"
    for i, f in enumerate(filas[:6]):
        unitario = f" ({f['precio_unitario']})" if f.get('precio_unitario') and len({x['medida'] for x in filas}) > 1 else ''
        etiqueta = ' ✅ más barato' if i == 0 and len(filas) > 1 else ''
        texto += f"• *{f['tienda']}*: ${f['precio']:.2f}{unitario}{etiqueta}\n   {_nombre_corto(f)} · {f['fecha'][8:10]}/{f['fecha'][5:7]}\n"
    if len(filas) > 1 and filas[0]['medida'] == filas[-1]['medida'] and filas[-1]['precio'] > filas[0]['precio']:
        texto += f"\n💰 Comprando en {filas[0]['tienda']} ahorras ${filas[-1]['precio'] - filas[0]['precio']:.2f}"
    return texto.strip()


def msg_entre_ciudades(consulta, filas):
    if not filas:
        return f"Todavía no tengo precios de *{consulta}* en ninguna ciudad. 📸 Mándame una factura y empiezo."
    texto = f"🌎 *{consulta}* por ciudad:\n\n"
    for f in filas[:8]:
        b = f['mas_barato']
        texto += (f"📍 *{f['ciudad']}*: desde ${b['precio']:.2f} en {b['tienda']} ({b['medida'] or 's/medida'})"
                  f"\n   promedio ${f['precio_prom']:.2f} en {f['tiendas']} tienda{'s' if f['tiendas'] != 1 else ''}\n")
    return texto.strip()


def msg_ranking(ciudad, filas):
    if not filas:
        return (f"Aún no tengo suficientes facturas de {ciudad} para comparar tiendas (necesito el mismo "
                f"producto en al menos 2 tiendas). 📸 ¡Sigue mandando tus facturas!")
    texto = f"🏆 Tiendas más baratas en {ciudad}:\n\n"
    for i, f in enumerate(filas[:6], 1):
        diferencia = (f['indice'] - 1) * 100
        comparado = f"{abs(diferencia):.0f}% {'más barato' if diferencia < 0 else 'más caro'} que el promedio" \
            if abs(diferencia) >= 0.5 else 'en el promedio'
        texto += f"{i}. *{f['tienda']}*: {comparado} ({f['productos']} producto{'s' if f['productos'] != 1 else ''})\n"
    return texto.strip()


def msg_factura(ciudad, tienda, guardados, ahorros, sin_precio=0):
    if guardados:
        texto = f"🛒 Guardé {guardados} precio{'s' if guardados != 1 else ''} de *{tienda}* para comparar en {ciudad}."
    else:
        texto = f"🛒 Esta factura de *{tienda}* ya la tenía guardada en {ciudad}."
    if sin_precio:
        texto += f"\n⚠️ {sin_precio} producto{'s' if sin_precio != 1 else ''} sin precio claro: no los usé."
    if ahorros:
        texto += "\n\n💡 Más barato en otras tiendas de tu ciudad:"
        for a in ahorros[:5]:
            art = a['articulo']
            marca = f" ({a['marca']})" if a['marca'] and a['marca'].lower() != art.get('marca', '').lower() else ''
            texto += (f"\n• {_nombre_corto(art)}: pagaste ${art['precio']:.2f}, en *{a['tienda']}* "
                      f"${a['precio']:.2f}{marca} (ahorras ${a['ahorro']:.2f})")
        total = sum(a['ahorro'] for a in ahorros)
        if len(ahorros) > 1:
            texto += f"\n💰 Ahorro posible: ${total:.2f}"
    elif guardados:
        texto += "\n✅ No encontré estos productos más baratos en otra tienda de tu ciudad."
    return texto


# ==================== INTENCIONES ====================

_RE_FIJAR_CIUDAD = re.compile(r'^(?:mi ciudad es|mi ciudad:|ciudad:|vivo en|cambiar? (?:mi )?ciudad a)\s+(.+)$')
_PALABRAS_RANKING = ('tiendas baratas', 'tienda barata', 'tiendas mas baratas', 'tienda mas barata',
                     'ranking de tiendas', 'que tienda es mas barata', 'cual tienda es mas barata',
                     'donde sale mas barato comprar', 'mejores tiendas')
_PALABRAS_BUSCAR = ('donde es mas barato', 'donde esta mas barato', 'donde sale mas barato',
                    'donde es mas barata', 'donde esta mas barata', 'donde sale mas barata',
                    'donde compro mas barato', 'precios del', 'precios de', 'precio del', 'precio de', 'costo del', 'costo de',
                    'cuanto cuesta', 'cuanto vale', 'comparar')
_TODAS = ('todas las ciudades', 'cada ciudad', 'las ciudades', 'otras ciudades', 'ciudades')


def es_ciudad(base, texto):
    slug, _ = normalizar_ciudad(texto)
    return bool(slug) and (slug in ciudades_con_datos(base) or slug.replace('_', ' ') in CIUDADES_CONOCIDAS
                           or slug in ALIAS_CIUDAD)


def parsear_consulta(base, mensaje):
    """Detecta pedidos del comparador. Devuelve dict con 'tipo' o None."""
    texto = re.sub(r'\s+', ' ', sin_acentos(mensaje).lower()).strip(' ¿?¡!.')
    m = _RE_FIJAR_CIUDAD.match(texto)
    if m:
        original = re.sub(r'\s+', ' ', mensaje).strip(' ¿?¡!.')
        return {'tipo': 'ciudad', 'ciudad': original[len(original) - len(m.group(1)):]}

    if any(p in texto for p in _PALABRAS_RANKING):
        ciudad = re.search(r'\ben ([a-z ]+)$', texto)
        return {'tipo': 'ranking', 'ciudad': ciudad.group(1) if ciudad and es_ciudad(base, ciudad.group(1)) else None}

    for palabra in _PALABRAS_BUSCAR:
        if palabra in texto:
            producto = texto.split(palabra, 1)[1].strip(' ,:¿?')
            producto = re.sub(r'^(el|la|los|las|un|una)\s+', '', producto)
            todas, ciudad = False, None
            for t in _TODAS:
                if producto.endswith(' en ' + t) or producto.endswith(' entre ' + t) or producto.endswith(' por ' + t):
                    todas, producto = True, re.sub(r'\s+(en|entre|por) ' + t + '$', '', producto)
                    break
            if not todas:
                m = re.search(r'\s+en ([a-z ]+)$', producto)
                if m and es_ciudad(base, m.group(1)):
                    ciudad, producto = m.group(1), producto[:m.start()]
            return {'tipo': 'buscar', 'producto': producto.strip(), 'ciudad': ciudad, 'todas': todas}
    return None
