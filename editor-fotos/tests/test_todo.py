"""Pruebas rapidas: python3 -m pytest -q"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_DB", ":memory:")
os.environ["FOTOS_GRATIS_DIA"] = "3"

import motor  # noqa: E402


def foto(oscura=False, w=600, h=400):
    rng = np.random.default_rng(1)
    x = np.linspace(0, 255, w)[None, :, None] * np.ones((h, 1, 3))
    x = x * np.array([1.0, 0.8, 0.6]) + rng.normal(0, 8, (h, w, 3))
    if oscura:
        x *= 0.35
    return np.clip(x, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("estilo", list(motor.ESTILOS))
def test_todos_los_estilos(estilo):
    out = motor.editar(foto(), estilo)
    assert out.size == (600, 400) and out.mode == "RGB"


def test_aclara_fotos_oscuras():
    antes = foto(oscura=True).mean()
    despues = np.array(motor.editar(foto(oscura=True), "natural")).mean()
    assert despues > antes * 1.6


def test_blanco_y_negro_sin_color():
    out = np.array(motor.editar(foto(), "blanco_negro")).astype(int)
    assert np.abs(out[..., 0] - out[..., 2]).max() <= 3


def test_recorte_y_ancho():
    out = motor.editar(foto(), "natural", recorte="1:1", ancho=200)
    assert out.size == (200, 200)
    assert motor.leer_ratio("no") is None
    with pytest.raises(ValueError):
        motor.leer_ratio("abc")


def test_marca_y_comparacion():
    img = motor.editar(foto(), "natural", marca="Estudio Lopez")
    comp = motor.antes_despues(foto(), img)
    assert comp.width > 0


def test_carga_png_transparente_y_exif(tmp_path):
    png = tmp_path / "t.png"
    Image.new("RGBA", (50, 40), (0, 0, 0, 0)).save(png)
    assert motor.cargar(png)[0, 0].tolist() == [255, 255, 255]  # fondo blanco

    jpg = tmp_path / "rotada.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # celular girado 90 grados
    Image.fromarray(foto(w=60, h=40)).save(jpg, exif=exif)
    assert motor.cargar(jpg).shape[:2] == (60, 40)


def test_nombres_unicos():
    from editor_fotos import nombres_unicos
    assert nombres_unicos([Path("a.png"), Path("a.jpg"), Path("b.jpg")]) == ["a.jpg", "a_2.jpg", "b.jpg"]


def test_creditos_del_bot():
    import bot
    db = bot.Datos(":memory:")
    db.usuario(1, "Ana")
    assert db.reservar(1, 2) == (2, 0)          # 2 gratis
    assert db.reservar(1, 5) == (1, 0)          # queda 1 gratis, sin creditos
    assert db.agregar_creditos(1, 10, admin=99) == 10
    assert db.reservar(1, 4) == (0, 4)
    db.devolver(1, 0, 1)                        # una fallo: se devuelve
    assert db.usuario(1)["creditos"] == 7
    db.preferencia(1, "marca", "Mi Estudio")
    assert db.usuario(1)["marca"] == "Mi Estudio"
    assert db.estadisticas()["vendidas"] == 10


def test_bot_arma_la_app(tmp_path):
    import bot
    app = bot.crear_app("123456:ABCDEF_token_de_prueba")
    assert len(app.handlers[0]) > 5
    ruta = tmp_path / "x.jpg"
    Image.fromarray(foto()).save(ruta)
    datos, comp = bot._editar_archivo(ruta, "vivido", {"recorte": "4:5", "ancho": 300}, "Gratis", True)
    assert datos[:2] == b"\xff\xd8" and comp[:2] == b"\xff\xd8"
