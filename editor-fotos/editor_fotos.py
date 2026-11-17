#!/usr/bin/env python3
"""Editor de fotos por lote (consola).

Uso:
    python3 editor_fotos.py ENTRADA SALIDA [--estilo calido] [--marca "Mi Estudio"]
                            [--logo logo.png] [--recorte 4:5] [--ancho 2000]
                            [--comparar] [--procesos 4]

Ejecuta `python3 editor_fotos.py --estilos` para ver los estilos.
"""
import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import motor


def nombres_unicos(fotos):
    """foto.png y foto.jpg darian el mismo foto.jpg: el segundo pasa a foto_2.jpg."""
    usados, salida = set(), []
    for f in fotos:
        nombre, n = f.stem, 2
        while nombre.lower() in usados:
            nombre, n = f"{f.stem}_{n}", n + 1
        usados.add(nombre.lower())
        salida.append(nombre + ".jpg")
    return salida


def procesar(ruta, destino, opciones):
    original = motor.cargar(ruta)
    editada = motor.editar(original, opciones["estilo"], opciones["recorte"],
                           opciones["ancho"], opciones["marca"], opciones["logo"])
    motor.guardar(editada, destino, opciones["calidad"])
    if opciones["comparar"]:
        comp = motor.antes_despues(original, editada)
        motor.guardar(comp, destino.parent / "comparaciones" / destino.name, 85)


def main():
    p = argparse.ArgumentParser(description="Editor de fotos por lote")
    p.add_argument("entrada", nargs="?")
    p.add_argument("salida", nargs="?")
    p.add_argument("--estilo", choices=motor.ESTILOS, default="natural")
    p.add_argument("--marca", help="Texto de marca de agua")
    p.add_argument("--logo", help="Logo PNG como marca de agua (en vez de texto)")
    p.add_argument("--recorte", help="Relacion de aspecto, ej. 4:5, 1:1 o 16:9")
    p.add_argument("--ancho", type=int, help="Ancho maximo en pixeles")
    p.add_argument("--calidad", type=int, default=92, help="Calidad JPG (60-100)")
    p.add_argument("--comparar", action="store_true", help="Crea imagenes antes/despues")
    p.add_argument("--procesos", type=int, default=os.cpu_count() or 1,
                   help="Fotos a la vez (por defecto, todos los nucleos)")
    p.add_argument("--estilos", action="store_true", help="Muestra los estilos y sale")
    args = p.parse_args()

    if args.estilos:
        for nombre, desc in motor.DESCRIPCIONES.items():
            print(f"  {nombre:<14} {desc}")
        return
    if not args.entrada or not args.salida:
        p.error("faltan las carpetas ENTRADA y SALIDA")
    if args.recorte:
        try:
            motor.leer_ratio(args.recorte)
        except ValueError:
            p.error("--recorte debe ser como 4:5 o 16:9")

    entrada, salida = Path(args.entrada), Path(args.salida)
    if not entrada.is_dir():
        sys.exit(f"No existe la carpeta {entrada}")
    salida.mkdir(parents=True, exist_ok=True)
    if args.comparar:
        (salida / "comparaciones").mkdir(exist_ok=True)

    ext = motor.extensiones_soportadas()
    fotos = sorted(f for f in entrada.iterdir() if f.is_file() and f.suffix.lower() in ext)
    if not fotos:
        sys.exit("No encontre fotos en la carpeta de entrada. Formatos: " + ", ".join(sorted(ext)))

    opciones = dict(estilo=args.estilo, recorte=args.recorte, ancho=args.ancho, marca=args.marca,
                    logo=args.logo, calidad=args.calidad, comparar=args.comparar)
    destinos = [salida / n for n in nombres_unicos(fotos)]

    errores, hechas = 0, 0
    with ProcessPoolExecutor(max_workers=max(1, args.procesos)) as pool:
        tareas = {pool.submit(procesar, f, d, opciones): f for f, d in zip(fotos, destinos)}
        for t in as_completed(tareas):
            hechas += 1
            foto = tareas[t]
            try:
                t.result()
                print(f"[{hechas}/{len(fotos)}] {foto.name} OK")
            except Exception as e:
                errores += 1
                print(f"[{hechas}/{len(fotos)}] {foto.name} ERROR: {e}")
    print(f"Listo: {len(fotos) - errores} editadas, {errores} con error. Carpeta: {salida}")


if __name__ == "__main__":
    main()
