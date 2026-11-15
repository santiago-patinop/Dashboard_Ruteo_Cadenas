"""Genera copias de prueba del archivo base en datos/pruebas/ (no modifica el original)."""
import random
import shutil
from pathlib import Path

from openpyxl import Workbook, load_workbook

RAIZ = Path(__file__).resolve().parent.parent
BASE = RAIZ / "datos" / "Reto_VRP_Medellin_Datos.xlsx"
SALIDA = RAIZ / "datos" / "pruebas"


def hoja_demanda(wb):
    return wb["Demanda"]


def columnas(ws):
    return {c.value: c.column for c in ws[1]}


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(2026)

    # a. Archivo tal como viene
    shutil.copy(BASE, SALIDA / "a_ejemplo_original.xlsx")

    # b. Viernes y Sábado llenos (valores sintéticos de magnitud similar)
    wb = load_workbook(BASE)
    ws = hoja_demanda(wb)
    col = columnas(ws)
    for fila in range(2, ws.max_row + 1):
        lun_jue = [ws.cell(fila, col[d]).value or 0 for d in ("Lunes", "Martes", "Miércoles", "Jueves")]
        media = sum(lun_jue) / 4
        ws.cell(fila, col["Viernes"]).value = round(max(0.3, media * rnd.uniform(0.85, 1.25)), 1)
        ws.cell(fila, col["Sábado"]).value = 0 if rnd.random() < 0.3 else round(max(0.3, media * rnd.uniform(0.7, 1.1)), 1)
    wb.save(SALIDA / "b_viernes_sabado_llenos.xlsx")

    # c. Libro con solo la hoja Demanda (Viernes y Sábado llenos)
    origen = hoja_demanda(load_workbook(SALIDA / "b_viernes_sabado_llenos.xlsx"))
    nuevo = Workbook()
    ws = nuevo.active
    ws.title = "Demanda"
    for fila in origen.iter_rows(values_only=True):
        ws.append(list(fila))
    nuevo.save(SALIDA / "c_solo_demanda.xlsx")

    # d. Un día con demanda total mayor a 28 t (Viernes = Lunes + 0,1 t por cliente -> 30,5 t)
    wb = load_workbook(BASE)
    ws = hoja_demanda(wb)
    col = columnas(ws)
    for fila in range(2, ws.max_row + 1):
        ws.cell(fila, col["Viernes"]).value = round((ws.cell(fila, col["Lunes"]).value or 0) + 0.1, 1)
    wb.save(SALIDA / "d_dia_infactible.xlsx")

    # e. Errores: columna faltante (Latitud en Clientes), texto en una demanda, nodo inexistente
    wb = load_workbook(BASE)
    wb["Clientes"].delete_cols(columnas(wb["Clientes"])["Latitud"])
    ws = hoja_demanda(wb)
    col = columnas(ws)
    ws.cell(5, col["Martes"]).value = "uno coma dos"
    ws.cell(ws.max_row + 1, 1).value = 45
    ws.cell(ws.max_row, 2).value = "Cliente inexistente"
    ws.cell(ws.max_row, col["Lunes"]).value = 0.8
    wb.save(SALIDA / "e_con_errores.xlsx")

    # f. Archivo que no es Excel (CSV renombrado y PDF falso)
    (SALIDA / "f_no_es_excel.xlsx").write_text("Nodo;Cliente;Lunes\n1;Éxito Mayorca;1,2\n", encoding="utf-8")
    (SALIDA / "f_documento.pdf").write_bytes(b"%PDF-1.4\n% archivo de prueba\n")
    print("Archivos generados en", SALIDA)


if __name__ == "__main__":
    main()
