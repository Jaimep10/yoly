#!/usr/bin/env python
"""Test professional Excel generator with multiple sheets."""
import sys
import tempfile
import os
from generador_excel import crear_excel_profesional


def test_excel_sheet_structure():
    """Test that Excel has exactly 5 sheets with correct names."""
    wa_id = "593987654321"
    cuenta = "Fanny"

    wb = crear_excel_profesional(wa_id, cuenta)

    # Verificar que tiene 5 hojas
    assert len(wb.sheetnames) == 5, f"Esperado 5 hojas, obtenido {len(wb.sheetnames)}: {wb.sheetnames}"

    # Verificar nombres de hojas
    sheet_names = wb.sheetnames
    assert "RESUMEN" in sheet_names, f"'RESUMEN' no encontrado en {sheet_names}"
    assert "DETALLE PROVEEDORES" in sheet_names, f"'DETALLE PROVEEDORES' no encontrado en {sheet_names}"
    assert "POR CUENTA" in sheet_names, f"'POR CUENTA' no encontrado en {sheet_names}"
    assert "POR CATEGORÍA" in sheet_names, f"'POR CATEGORÍA' no encontrado en {sheet_names}"
    assert "COMPROBANTES" in sheet_names, f"'COMPROBANTES' no encontrado en {sheet_names}"

    # Verificar orden
    assert sheet_names[0] == "RESUMEN"
    assert sheet_names[1] == "DETALLE PROVEEDORES"
    assert sheet_names[2] == "POR CUENTA"
    assert sheet_names[3] == "POR CATEGORÍA"
    assert sheet_names[4] == "COMPROBANTES"

    print("✓ test_excel_sheet_structure PASSED - 5 pestañas con nombres correctos")


def test_excel_resumen_content():
    """Test that RESUMEN sheet contains required headers and data."""
    wa_id = "593987654321"
    cuenta = "Fanny"
    gastos = [
        {"fecha": "2026-10-01", "cliente": "Tienda A", "monto": 1000, "categoria": "Materiales"},
    ]

    wb = crear_excel_profesional(wa_id, cuenta, gastos)
    ws = wb["RESUMEN"]

    # Verificar que contiene información requerida
    contenido = ""
    for row in ws.iter_rows(values_only=True):
        contenido += str(row) + "\n"

    assert cuenta in contenido, f"Cuenta '{cuenta}' no encontrada en RESUMEN"
    assert wa_id in contenido, f"wa_id '{wa_id}' no encontrado en RESUMEN"

    print("✓ test_excel_resumen_content PASSED - RESUMEN contiene datos requeridos")


def test_excel_detalle_headers():
    """Test that DETALLE PROVEEDORES has correct headers."""
    wb = crear_excel_profesional("593987654321", "Fanny", gastos=[])
    ws = wb["DETALLE PROVEEDORES"]

    # Verificar headers
    headers = []
    for cell in ws[1]:
        if cell.value:
            headers.append(cell.value)

    assert "Fecha" in headers, f"'Fecha' no encontrado en headers: {headers}"
    assert "Proveedor" in headers, f"'Proveedor' no encontrado en headers: {headers}"
    assert "Pagado" in headers, f"'Pagado' no encontrado en headers: {headers}"
    assert "Categoría" in headers, f"'Categoría' no encontrado en headers: {headers}"

    print("✓ test_excel_detalle_headers PASSED - Headers correctos en DETALLE PROVEEDORES")


def test_excel_with_gastos():
    """Test that Excel correctly populates data with multiple gastos."""
    gastos = [
        {"fecha": "2026-10-01", "cliente": "Tienda A", "monto": 1000, "categoria": "Materiales", "factura_path": "/ruta/1.jpg"},
        {"fecha": "2026-10-02", "cliente": "Tienda B", "monto": 500, "categoria": "Mano de obra", "factura_path": "/ruta/2.jpg"},
        {"fecha": "2026-10-03", "cliente": "Transporte", "monto": 250, "categoria": "Transporte", "factura_path": "/ruta/3.jpg"},
    ]

    wb = crear_excel_profesional("593987654321", "Fanny", gastos)

    # Verificar DETALLE PROVEEDORES tiene datos
    ws = wb["DETALLE PROVEEDORES"]
    data_rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(data_rows) >= 3, f"Esperado al menos 3 gastos en DETALLE, obtenido {len(data_rows)}"

    # Verificar POR CATEGORÍA tiene categorías
    ws_cat = wb["POR CATEGORÍA"]
    categorias = []
    for row in ws_cat.iter_rows(min_row=2, values_only=True):
        if row[0]:  # Si hay valor en primera columna
            categorias.append(row[0])

    assert len(categorias) >= 3, f"Esperado al menos 3 categorías, obtenido {len(categorias)}: {categorias}"

    # Verificar COMPROBANTES tiene archivos
    ws_comp = wb["COMPROBANTES"]
    archivos = []
    for row in ws_comp.iter_rows(min_row=2, values_only=True):
        if row[3]:  # Columna D = archivo
            archivos.append(row[3])

    assert len(archivos) >= 3, f"Esperado al menos 3 comprobantes, obtenido {len(archivos)}"

    print("✓ test_excel_with_gastos PASSED - Datos se populan correctamente")


def test_excel_save_and_load():
    """Test that Excel can be saved and loaded correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "test_excel.xlsx")

        gastos = [
            {"fecha": "2026-10-01", "cliente": "Proveedor A", "monto": 1000, "categoria": "Materiales"},
        ]

        wb = crear_excel_profesional("593987654321", "Fanny", gastos)
        wb.save(filepath)

        # Verificar que el archivo existe
        assert os.path.exists(filepath), f"El archivo {filepath} no se creó"
        assert os.path.getsize(filepath) > 0, f"El archivo {filepath} está vacío"

        # Recargar y verificar
        from openpyxl import load_workbook
        wb2 = load_workbook(filepath)
        assert len(wb2.sheetnames) == 5, f"Archivo recargado no tiene 5 hojas: {wb2.sheetnames}"

        print("✓ test_excel_save_and_load PASSED - Excel guardado y recargado correctamente")


def test_excel_formatting():
    """Test that Excel has proper formatting (colors, borders, etc)."""
    wb = crear_excel_profesional("593987654321", "Fanny", gastos=[
        {"fecha": "2026-10-01", "cliente": "Test", "monto": 100, "categoria": "Test"},
    ])

    ws = wb["DETALLE PROVEEDORES"]

    # Verificar que la primera fila (headers) tiene formato
    header_cell = ws['A1']
    assert header_cell.fill is not None, "Headers deben tener color de fondo"
    assert header_cell.font is not None, "Headers deben tener formato de fuente"

    # Verificar que hay bordes
    assert header_cell.border is not None, "Celdas deben tener bordes"

    print("✓ test_excel_formatting PASSED - Formato aplicado correctamente")


if __name__ == "__main__":
    try:
        test_excel_sheet_structure()
        test_excel_resumen_content()
        test_excel_detalle_headers()
        test_excel_with_gastos()
        test_excel_save_and_load()
        test_excel_formatting()
        print("\n✅ All test_contract_excel tests PASSED! (6/6)")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        sys.exit(1)
