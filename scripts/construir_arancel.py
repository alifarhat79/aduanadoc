"""
Genera app/recursos/arancel/arancel_vigente.json.gz, el arancel que viene incluido con el sistema
(cada PC lo carga en su base local la primera vez que se usa).

Uso (desde la carpeta del proyecto):
    python scripts/construir_arancel.py --pdf "Arancel Nacional Vigente_octubre 2025_1.pdf" \
        --excel "AEC 2017 (13-04-2020) - ACTUALIZADOOOOO.xls" \
        --nombre "Arancel Nacional Vigente — octubre 2025 (MEF)" --vigencia 2025-10-01 \
        --url "https://www.mef.gov.py/..."

Solo se guardan datos públicos del arancel; ningún dato de despachos ni de clientes.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.services.arancel_importer import (  # noqa: E402
    FUENTE_EXCEL, construir_dataset, guardar_dataset, parsear_excel_aec, parsear_pdf_mef, validar_dataset,
)

DESTINO = BASE / "app" / "recursos" / "arancel" / "arancel_vigente.json.gz"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True, help="PDF oficial 'Arancel Nacional Vigente' del MEF")
    ap.add_argument("--excel", help="Excel AEC con IVA/Renta/ISC y notas legales (opcional)")
    ap.add_argument("--nombre", required=True)
    ap.add_argument("--vigencia", required=True, help="AAAA-MM-DD")
    ap.add_argument("--url", default="", help="Dirección pública del PDF oficial")
    ap.add_argument("--salida", default=str(DESTINO))
    a = ap.parse_args()

    datetime.strptime(a.vigencia, "%Y-%m-%d")
    print("Leyendo PDF del MEF...")
    nodos = parsear_pdf_mef(a.pdf)
    excel = None
    if a.excel:
        print("Leyendo Excel AEC...")
        excel = parsear_excel_aec(a.excel)
    fuente = f"{Path(a.pdf).name}" + (f" ({a.url})" if a.url else "")
    if a.excel:
        fuente += f" + {Path(a.excel).name} (IVA, Renta, ISC, notas legales)"
    ds = construir_dataset(nodos, excel, meta={"nombre": a.nombre, "fecha_vigencia": a.vigencia, "fuente": fuente},
                           fuente_complemento=FUENTE_EXCEL)
    validar_dataset(ds)
    guardar_dataset(ds, a.salida)
    print("Listo:", a.salida, "| tamaño:", round(Path(a.salida).stat().st_size / 1e6, 2), "MB")
    print("Resumen:", ds["resumen"])


if __name__ == "__main__":
    main()
