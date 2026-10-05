"""Generador de reportes Excel profesionales con múltiples pestañas."""
import json
import os
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Colores profesionales
HEADER_COLOR = "1E40AF"  # Azul oscuro
HEADER_FONT_COLOR = "FFFFFF"  # Blanco
LIGHT_GRAY = "F3F4F6"  # Gris claro para subrrenglones


def crear_border():
    """Crea bordes para las celdas."""
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    return thin_border


def crear_excel_profesional(wa_id, cuenta_activa, gastos=None, cobro=None):
    """
    Crea un Excel profesional con 5 pestañas:
    1. RESUMEN: Logo, Cliente, Cuenta, Período, Totales
    2. DETALLE PROVEEDORES: Fecha | Proveedor | Facturado | Pagado | Saldo | Categoría
    3. POR CUENTA: Resumen si hay múltiples cuentas
    4. POR CATEGORÍA: Materiales/Mano de obra/Transporte
    5. COMPROBANTES: Lista de fotos/archivos

    Args:
        wa_id: Teléfono del usuario (limpio, ej: 593987654321)
        cuenta_activa: Nombre de la cuenta actual (ej: "Fanny")
        gastos: Lista de gastos (dict con id, monto, cliente, fecha, categoria, etc)
        cobro: Datos de cobro/deuda (opcional)

    Returns:
        Workbook object (puede ser guardado con wb.save('archivo.xlsx'))
    """
    wb = Workbook()

    # Eliminar hoja por defecto
    if 'Sheet' in wb.sheetnames:
        wb.remove(wb['Sheet'])

    # Crear las 5 pestañas
    ws_resumen = wb.create_sheet("RESUMEN", 0)
    ws_detalle = wb.create_sheet("DETALLE PROVEEDORES", 1)
    ws_cuenta = wb.create_sheet("POR CUENTA", 2)
    ws_categoria = wb.create_sheet("POR CATEGORÍA", 3)
    ws_comprobantes = wb.create_sheet("COMPROBANTES", 4)

    # Cargar gastos si no se proporcionan
    if gastos is None:
        gastos = []
    if cobro is None:
        cobro = {}

    # Llenar cada pestaña
    _llenar_resumen(ws_resumen, wa_id, cuenta_activa, gastos, cobro)
    _llenar_detalle_proveedores(ws_detalle, gastos)
    _llenar_por_cuenta(ws_cuenta, wa_id, cuenta_activa, gastos)
    _llenar_por_categoria(ws_categoria, gastos)
    _llenar_comprobantes(ws_comprobantes, gastos)

    return wb


def _llenar_resumen(ws, wa_id, cuenta_activa, gastos, cobro):
    """Pestaña 1: RESUMEN con cliente, cuenta, período y totales."""
    border = crear_border()
    header_fill = PatternFill(start_color=HEADER_COLOR, end_color=HEADER_COLOR, fill_type="solid")
    header_font = Font(color=HEADER_FONT_COLOR, bold=True, size=11)

    # Headers principales
    ws['A1'] = "RESUMEN FINANCIERO"
    ws['A1'].font = Font(bold=True, size=14)

    # Cliente y Cuenta
    ws['A3'] = "Cliente:"
    ws['A3'].font = Font(bold=True)
    ws['B3'] = cuenta_activa

    ws['A4'] = "Teléfono:"
    ws['A4'].font = Font(bold=True)
    ws['B4'] = wa_id

    ws['A5'] = "Período:"
    ws['A5'].font = Font(bold=True)
    ws['B5'] = datetime.now().strftime("%b %Y").capitalize()

    # Totales
    row = 7
    ws[f'A{row}'] = "RESUMEN"
    ws[f'A{row}'].font = header_font
    ws[f'A{row}'].fill = header_fill
    ws[f'B{row}'] = "MONTO"
    ws[f'B{row}'].font = header_font
    ws[f'B{row}'].fill = header_fill

    for col in ['A', 'B']:
        ws[f'{col}{row}'].border = border

    row = 8
    total_gastos = sum(g.get('monto', 0) for g in gastos if isinstance(g, dict))

    ws[f'A{row}'] = "Total Gastos"
    ws[f'B{row}'] = total_gastos
    ws[f'B{row}'].number_format = '$#,##0.00'

    for col in ['A', 'B']:
        ws[f'{col}{row}'].border = border

    # Si hay cobro
    if cobro and cobro.get('deuda'):
        row = 9
        ws[f'A{row}'] = "Deuda Total"
        ws[f'B{row}'] = cobro.get('deuda', 0)
        ws[f'B{row}'].number_format = '$#,##0.00'
        for col in ['A', 'B']:
            ws[f'{col}{row}'].border = border

        row = 10
        ws[f'A{row}'] = "Pagado"
        pagado = sum(p.get('monto', 0) for p in cobro.get('pagos', []) if isinstance(p, dict))
        ws[f'B{row}'] = pagado
        ws[f'B{row}'].number_format = '$#,##0.00'
        for col in ['A', 'B']:
            ws[f'{col}{row}'].border = border

        row = 11
        ws[f'A{row}'] = "Saldo Pendiente"
        saldo = cobro.get('deuda', 0) - pagado
        ws[f'B{row}'] = saldo
        ws[f'B{row}'].number_format = '$#,##0.00'
        ws[f'B{row}'].font = Font(bold=True, color="D32F2F")
        for col in ['A', 'B']:
            ws[f'{col}{row}'].border = border

    # Ancho de columnas
    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 15


def _llenar_detalle_proveedores(ws, gastos):
    """Pestaña 2: DETALLE PROVEEDORES con tabla de gastos."""
    border = crear_border()
    header_fill = PatternFill(start_color=HEADER_COLOR, end_color=HEADER_COLOR, fill_type="solid")
    header_font = Font(color=HEADER_FONT_COLOR, bold=True)

    # Headers
    headers = ["Fecha", "Proveedor", "Facturado", "Pagado", "Saldo", "Categoría"]
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center')

    # Datos
    for row_num, gasto in enumerate(gastos, 2):
        if not isinstance(gasto, dict):
            continue

        # Aplicar color alternado
        if row_num % 2 == 0:
            cell_fill = PatternFill(start_color=LIGHT_GRAY, end_color=LIGHT_GRAY, fill_type="solid")
        else:
            cell_fill = None

        data = [
            gasto.get('fecha', ''),
            gasto.get('cliente', ''),
            gasto.get('monto', 0),
            gasto.get('monto', 0),  # Asumimos que pagado = monto
            0,  # Saldo (para futuro)
            gasto.get('categoria', 'Otro')
        ]

        for col_num, value in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col_num)
            cell.value = value
            cell.border = border
            if cell_fill:
                cell.fill = cell_fill

            # Formato para números
            if col_num in [3, 4, 5]:
                cell.number_format = '$#,##0.00'

    # Totales en fila final
    if gastos:
        total_row = len(gastos) + 2
        ws[f'A{total_row}'] = "TOTAL"
        ws[f'A{total_row}'].font = Font(bold=True)

        # Suma automática para columna "Pagado"
        ws[f'D{total_row}'] = f"=SUM(D2:D{total_row - 1})"
        ws[f'D{total_row}'].font = Font(bold=True)
        ws[f'D{total_row}'].number_format = '$#,##0.00'

        for col in range(1, 7):
            cell = ws.cell(row=total_row, column=col)
            cell.border = border
            cell.fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")

    # Ajustar ancho de columnas
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 20
    ws.column_dimensions['C'].width = 12
    ws.column_dimensions['D'].width = 12
    ws.column_dimensions['E'].width = 12
    ws.column_dimensions['F'].width = 15

    # Autofiltro
    if gastos:
        ws.auto_filter.ref = f"A1:F{len(gastos) + 1}"


def _llenar_por_cuenta(ws, wa_id, cuenta_activa, gastos):
    """Pestaña 3: POR CUENTA (resumen por cuenta si el usuario tiene múltiples)."""
    border = crear_border()
    header_fill = PatternFill(start_color=HEADER_COLOR, end_color=HEADER_COLOR, fill_type="solid")
    header_font = Font(color=HEADER_FONT_COLOR, bold=True)

    # Headers
    headers = ["Cuenta", "Total"]
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border

    # Por ahora solo la cuenta activa
    ws['A2'] = cuenta_activa
    ws['B2'] = sum(g.get('monto', 0) for g in gastos if isinstance(g, dict))
    ws['B2'].number_format = '$#,##0.00'

    for col in ['A', 'B']:
        ws[f'{col}2'].border = border

    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 15


def _llenar_por_categoria(ws, gastos):
    """Pestaña 4: POR CATEGORÍA (resumen por tipo de gasto)."""
    border = crear_border()
    header_fill = PatternFill(start_color=HEADER_COLOR, end_color=HEADER_COLOR, fill_type="solid")
    header_font = Font(color=HEADER_FONT_COLOR, bold=True)

    # Headers
    headers = ["Categoría", "Cantidad", "Total"]
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border

    # Agrupar por categoría
    categorias = {}
    for gasto in gastos:
        if isinstance(gasto, dict):
            cat = gasto.get('categoria', 'Otro')
            if cat not in categorias:
                categorias[cat] = {'cantidad': 0, 'total': 0}
            categorias[cat]['cantidad'] += 1
            categorias[cat]['total'] += gasto.get('monto', 0)

    # Mostrar categorías
    row = 2
    for categoria, datos in sorted(categorias.items()):
        ws[f'A{row}'] = categoria
        ws[f'B{row}'] = datos['cantidad']
        ws[f'C{row}'] = datos['total']
        ws[f'C{row}'].number_format = '$#,##0.00'

        for col in ['A', 'B', 'C']:
            ws[f'{col}{row}'].border = border

        row += 1

    # Totales
    if categorias:
        ws[f'A{row}'] = "TOTAL"
        ws[f'A{row}'].font = Font(bold=True)
        ws[f'B{row}'] = f"=SUM(B2:B{row - 1})"
        ws[f'B{row}'].font = Font(bold=True)
        ws[f'C{row}'] = f"=SUM(C2:C{row - 1})"
        ws[f'C{row}'].font = Font(bold=True)
        ws[f'C{row}'].number_format = '$#,##0.00'

        for col in ['A', 'B', 'C']:
            ws[f'{col}{row}'].border = border
            ws[f'{col}{row}'].fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")

    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 15


def _llenar_comprobantes(ws, gastos):
    """Pestaña 5: COMPROBANTES (lista de archivos/fotos)."""
    border = crear_border()
    header_fill = PatternFill(start_color=HEADER_COLOR, end_color=HEADER_COLOR, fill_type="solid")
    header_font = Font(color=HEADER_FONT_COLOR, bold=True)

    # Headers
    headers = ["Fecha", "Cliente", "Monto", "Archivo"]
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border

    # Datos de comprobantes
    row = 2
    for gasto in gastos:
        if isinstance(gasto, dict):
            ws[f'A{row}'] = gasto.get('fecha', '')
            ws[f'B{row}'] = gasto.get('cliente', '')
            ws[f'C{row}'] = gasto.get('monto', 0)
            ws[f'C{row}'].number_format = '$#,##0.00'

            # Path del archivo
            factura_path = gasto.get('factura_path', '')
            if factura_path:
                # Extraer solo el nombre del archivo
                filename = os.path.basename(factura_path)
                ws[f'D{row}'] = filename

            for col in ['A', 'B', 'C', 'D']:
                ws[f'{col}{row}'].border = border

            row += 1

    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 20
    ws.column_dimensions['C'].width = 12
    ws.column_dimensions['D'].width = 30


if __name__ == "__main__":
    # Prueba rápida
    wb = crear_excel_profesional("593987654321", "Fanny", gastos=[
        {"fecha": "2026-10-01", "cliente": "Tienda A", "monto": 100, "categoria": "Materiales", "factura_path": "/ruta/factura1.jpg"},
        {"fecha": "2026-10-02", "cliente": "Tienda B", "monto": 250, "categoria": "Materiales", "factura_path": "/ruta/factura2.jpg"},
    ])
    wb.save('/tmp/test_excel.xlsx')
    print("✓ Excel de prueba generado en /tmp/test_excel.xlsx")
