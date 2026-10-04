#!/usr/bin/env python3
"""Test para validar que extraer_ingresos suma múltiples fuentes correctamente."""

from main import extraer_ingresos

def test_extraer_ingresos_multiples_fuentes():
    """Test que valida la suma de múltiples fuentes de ingreso."""

    input_texto = "Trabajo 120 horas al mes y gano 17 la hora, 400 a la semana de otro trabajo, 200 al mes de otro negocio"

    # Calcular resultado esperado:
    # Patrón 1: 120 horas * 17 = 2040
    # Patrón 2: 400 * 4.333 = 1733.2
    # Patrón 3: 200 = 200
    # Total: 3973.2

    resultado = extraer_ingresos(input_texto)
    esperado = 3973.2

    print(f"\n{'='*60}")
    print(f"TEST: extraer_ingresos() - Múltiples fuentes")
    print(f"{'='*60}")
    print(f"\nInput:")
    print(f"  {input_texto}")
    print(f"\nDesglose esperado:")
    print(f"  Patrón 1 (120 horas * 17): $2,040.00")
    print(f"  Patrón 2 (400 * 4.333):    $1,733.20")
    print(f"  Patrón 3 (200 al mes):     $  200.00")
    print(f"  {'─'*40}")
    print(f"  Total esperado:            ${esperado:,.2f}")
    print(f"\nResultado obtenido:        ${resultado:,.2f}")

    # Validar con tolerancia de 0.10 por redondeo
    if abs(resultado - esperado) < 0.10:
        print(f"\n✅ TEST PASADO")
        return True
    else:
        print(f"\n❌ TEST FALLIDO")
        print(f"Diferencia: ${abs(resultado - esperado):,.2f}")
        return False

if __name__ == "__main__":
    test_extraer_ingresos_multiples_fuentes()
