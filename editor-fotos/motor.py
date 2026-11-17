"""Motor de edicion de fotos (lo usan el script de consola y el bot).

Todo el ajuste de tono y color se hace en espacio LAB con numeros decimales:
- L (luz) se trata aparte del color, asi subir el brillo no lava los colores
  y dar nitidez no crea bordes de colores raros.
- Se trabaja en decimales de principio a fin y se convierte a 8 bits una
  sola vez al final, para no perder calidad en cada paso.
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

# Formatos opcionales: HEIC (iPhone) y RAW (camaras). Si no estan instalados,
# el resto sigue funcionando.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC = True
except ImportError:
    HEIC = False

try:
    import rawpy
except ImportError:
    rawpy = None

EXT_NORMALES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
EXT_HEIC = {".heic", ".heif"}
EXT_RAW = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}


def extensiones_soportadas():
    ext = set(EXT_NORMALES)
    if HEIC:
        ext |= EXT_HEIC
    if rawpy:
        ext |= EXT_RAW
    return ext


# Parametros de cada estilo:
#   brillo      1.0 = igual; >1 aclara los tonos medios sin quemar los blancos
#   contraste   1.0 = igual; curva en S suave (no recorta negros ni blancos)
#   saturacion  1.0 = igual; >1 sube sobre todo los colores apagados (protege la piel)
#   calidez     >0 mas calido (amarillo), <0 mas frio (azul)
#   nitidez     0 a 1, se aplica solo a la luz
#   detalle     0 a 1, recupera sombras y luces locales (ventanas, cielos)
#   balance     0 a 1, cuanto se corrige el tono de color dominante (luz amarilla, etc.)
ESTILOS = {
    "natural":      dict(brillo=1.00, contraste=1.05, saturacion=1.05, calidez=0,   nitidez=0.4, detalle=0.25, balance=0.5),
    "calido":       dict(brillo=1.05, contraste=1.08, saturacion=1.10, calidez=10,  nitidez=0.4, detalle=0.25, balance=0.3),
    "frio":         dict(brillo=1.00, contraste=1.10, saturacion=0.95, calidez=-10, nitidez=0.4, detalle=0.25, balance=0.5),
    "vivido":       dict(brillo=1.03, contraste=1.15, saturacion=1.35, calidez=3,   nitidez=0.6, detalle=0.35, balance=0.5),
    "inmobiliaria": dict(brillo=1.18, contraste=1.08, saturacion=1.12, calidez=0,   nitidez=0.7, detalle=0.60, balance=0.8),
    "producto":     dict(brillo=1.12, contraste=1.10, saturacion=1.05, calidez=0,   nitidez=0.8, detalle=0.20, balance=0.9),
    "boda":         dict(brillo=1.08, contraste=0.95, saturacion=0.92, calidez=6,   nitidez=0.25, detalle=0.30, balance=0.4),
    "blanco_negro": dict(brillo=1.02, contraste=1.20, saturacion=0.0, calidez=0,   nitidez=0.5, detalle=0.40, balance=0.0),
}

DESCRIPCIONES = {
    "natural": "Corrige luz y color sin que se note",
    "calido": "Tonos dorados, ideal para retratos y comida",
    "frio": "Tonos azulados y limpios",
    "vivido": "Colores intensos para redes sociales",
    "inmobiliaria": "Interiores claros, recupera ventanas",
    "producto": "Blancos limpios y mucho detalle (tiendas)",
    "boda": "Suave, luminoso y calido",
    "blanco_negro": "Blanco y negro con contraste",
}


# ---------------------------------------------------------------- carga

def cargar(ruta):
    """Abre una foto y la devuelve como arreglo RGB de 8 bits (respeta la rotacion EXIF)."""
    ruta = Path(ruta)
    if ruta.suffix.lower() in EXT_RAW:
        if not rawpy:
            raise ValueError("para archivos RAW instala rawpy: pip install rawpy")
        with rawpy.imread(str(ruta)) as raw:
            return raw.postprocess(use_camera_wb=True, output_bps=8, no_auto_bright=False)
    with Image.open(ruta) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            # Fondo blanco para PNG con transparencia (si no, queda negro).
            im = im.convert("RGBA")
            fondo = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(fondo, im)
        return np.array(im.convert("RGB"))


# ---------------------------------------------------------------- ajustes

def _suavizado(x):
    """Curva en S (smoothstep): oscurece sombras y aclara luces sin recortar."""
    return x * x * (3.0 - 2.0 * x)


def ajustar(rgb, brillo=1.0, contraste=1.0, saturacion=1.0, calidez=0.0,
            nitidez=0.0, detalle=0.0, balance=0.0, auto=True):
    """Aplica todos los ajustes a una imagen RGB uint8 y devuelve RGB uint8."""
    f = rgb.astype(np.float32) / 255.0
    lab = cv2.cvtColor(f, cv2.COLOR_RGB2LAB)  # L: 0..100, a/b: aprox -127..127
    L = lab[:, :, 0] / 100.0
    a = lab[:, :, 1]
    b = lab[:, :, 2]

    # 1) Balance de blancos: mide el tono solo en zonas casi neutras (paredes,
    #    blancos, grises) para que un traje naranja o un cielo azul no engañen
    #    la correccion. La correccion maxima esta limitada.
    if balance > 0:
        croma = np.sqrt(a * a + b * b)
        neutros = (croma < 18) & (L > 0.25) & (L < 0.97)
        if neutros.mean() > 0.03:
            da = float(np.clip(a[neutros].mean(), -10, 10))
            db = float(np.clip(b[neutros].mean(), -12, 12))
            a -= da * balance
            b -= db * balance

    if auto:
        # 2a) Niveles: si la foto no usa todo el rango (gris, lavada o subexpuesta),
        #     estira del punto mas oscuro al mas claro, sin exagerar.
        muestra = L[::4, ::4]
        bajo, alto = np.percentile(muestra, [0.5, 99.5])
        rango = max(alto - bajo, 1.0 / 2.5)
        if rango < 0.92:
            estirada = np.clip((L - bajo) / rango, 0, 1)
            L = L * 0.2 + estirada * 0.8

        # 2b) Exposicion con curva gamma: lleva el promedio hacia tonos medios.
        #     A diferencia de multiplicar, la gamma nunca quema los blancos.
        media = float(np.clip(L.mean(), 0.02, 0.98))
        gamma = np.clip(np.log(0.47) / np.log(media), 0.5, 1.6)
        gamma = 1.0 + (gamma - 1.0) * 0.8  # correccion parcial, mas natural
        L = np.power(L, gamma)

    # 3) Brillo del estilo (tambien con gamma).
    if brillo != 1.0:
        L = np.power(L, 1.0 / brillo)

    # 4) Detalle local (CLAHE): recupera sombras y luces por zonas.
    if detalle > 0:
        L16 = (np.clip(L, 0, 1) * 65535).astype(np.uint16)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        local = clahe.apply(L16).astype(np.float32) / 65535.0
        L = L * (1 - detalle) + local * detalle

    # 5) Contraste con curva en S.
    if contraste != 1.0:
        L = np.clip(L, 0, 1)
        L = L + (contraste - 1.0) * 3.0 * (_suavizado(L) - L)

    # 6) Calidez: mueve el eje azul-amarillo (y un poco el verde-magenta).
    if calidez:
        b += calidez * 0.6
        a += calidez * 0.1

    # 7) Saturacion tipo "vibrance": sube mas los colores apagados que los
    #    ya intensos, para que la piel no se vea anaranjada.
    if saturacion != 1.0:
        if saturacion > 1.0:
            croma = np.sqrt(a * a + b * b)
            factor = 1.0 + (saturacion - 1.0) * (1.0 - np.clip(croma / 60.0, 0, 1))
        else:
            factor = saturacion
        a *= factor
        b *= factor

    # 8) Nitidez (mascara de enfoque) solo en la luz; el radio crece con la foto.
    if nitidez > 0:
        sigma = max(1.0, max(L.shape) / 1500.0)
        suave = cv2.GaussianBlur(L, (0, 0), sigma)
        L = L + nitidez * (L - suave)

    lab[:, :, 0] = np.clip(L, 0, 1) * 100.0
    lab[:, :, 1] = a
    lab[:, :, 2] = b
    salida = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return (np.clip(salida, 0, 1) * 255.0 + 0.5).astype(np.uint8)


def recortar(img, ratio):
    """Recorta al centro a una relacion de aspecto (ancho/alto)."""
    h, w = img.shape[:2]
    if w / h > ratio:
        nw = int(round(h * ratio))
        x = (w - nw) // 2
        return img[:, x:x + nw]
    nh = int(round(w / ratio))
    y = (h - nh) // 2
    return img[y:y + nh, :]


def leer_ratio(texto):
    """Convierte '4:5' en 0.8. Devuelve None si el texto esta vacio o es 'no'."""
    if not texto or texto.lower() in ("no", "ninguno", "original"):
        return None
    a, b = texto.replace("x", ":").split(":")
    a, b = float(a), float(b)
    if a <= 0 or b <= 0:
        raise ValueError("la proporcion debe ser positiva, ej. 4:5")
    return a / b


def redimensionar(img, ancho_max):
    if ancho_max and img.shape[1] > ancho_max:
        escala = ancho_max / img.shape[1]
        img = cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    return img


# ---------------------------------------------------------------- marca de agua

def _fuente(tam):
    for nombre in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            pass
    return ImageFont.load_default(size=tam)


def marca_de_agua(pil_img, texto=None, logo=None, opacidad=170):
    """Pone texto (con sombra, se lee sobre fondos claros) o un logo PNG abajo a la derecha."""
    img = pil_img.convert("RGBA")
    capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
    margen = max(10, img.width // 50)

    if logo:
        with Image.open(logo) as lg:
            lg = lg.convert("RGBA")
            ancho = max(40, img.width // 6)
            lg = lg.resize((ancho, max(1, int(lg.height * ancho / lg.width))), Image.LANCZOS)
            alfa = lg.getchannel("A").point(lambda v: v * opacidad // 255)
            lg.putalpha(alfa)
            capa.paste(lg, (img.width - lg.width - margen, img.height - lg.height - margen), lg)
    elif texto:
        draw = ImageDraw.Draw(capa)
        tam = max(16, img.width // 30)
        fuente = _fuente(tam)
        caja = draw.textbbox((0, 0), texto, font=fuente)
        tw, th = caja[2] - caja[0], caja[3] - caja[1]
        x = img.width - tw - margen - caja[0]
        y = img.height - th - margen - caja[1]
        sombra = max(1, tam // 15)
        draw.text((x + sombra, y + sombra), texto, font=fuente, fill=(0, 0, 0, opacidad // 2))
        draw.text((x, y), texto, font=fuente, fill=(255, 255, 255, opacidad))

    return Image.alpha_composite(img, capa).convert("RGB")


# ---------------------------------------------------------------- antes / despues

def antes_despues(original, editada, alto=900):
    """Imagen lado a lado (o arriba/abajo si la foto es horizontal) para mostrar al cliente."""
    def a_pil(x):
        return x if isinstance(x, Image.Image) else Image.fromarray(x)

    o, e = a_pil(original), a_pil(editada)
    # Mismo encuadre: si la editada se recorto, se recorta igual el original.
    if abs(o.width / o.height - e.width / e.height) > 0.01:
        o = Image.fromarray(recortar(np.array(o), e.width / e.height))
    horizontal = e.width > e.height
    if horizontal:
        ancho = alto * 4 // 3
        o = ImageOps.contain(o, (ancho, alto // 2))
        e = ImageOps.contain(e, (ancho, alto // 2))
        lienzo = Image.new("RGB", (max(o.width, e.width), o.height + e.height + 6), "white")
        lienzo.paste(o, (0, 0))
        lienzo.paste(e, (0, o.height + 6))
        pos_o, pos_e = (10, 10), (10, o.height + 16)
    else:
        o = ImageOps.contain(o, (alto, alto))
        e = ImageOps.contain(e, (alto, alto))
        lienzo = Image.new("RGB", (o.width + e.width + 6, max(o.height, e.height)), "white")
        lienzo.paste(o, (0, 0))
        lienzo.paste(e, (o.width + 6, 0))
        pos_o, pos_e = (10, 10), (o.width + 16, 10)

    draw = ImageDraw.Draw(lienzo)
    fuente = _fuente(max(18, lienzo.width // 40))
    for pos, txt in ((pos_o, "ANTES"), (pos_e, "DESPUES")):
        caja = draw.textbbox(pos, txt, font=fuente)
        draw.rectangle((caja[0] - 6, caja[1] - 4, caja[2] + 6, caja[3] + 4), fill=(0, 0, 0))
        draw.text(pos, txt, font=fuente, fill=(255, 255, 255))
    return lienzo


# ---------------------------------------------------------------- todo junto

def editar(rgb, estilo="natural", recorte=None, ancho=None, marca=None, logo=None):
    """Edita un arreglo RGB y devuelve una imagen PIL lista para guardar."""
    if estilo not in ESTILOS:
        raise ValueError(f"estilo desconocido: {estilo}")
    img = ajustar(rgb, **ESTILOS[estilo])
    ratio = leer_ratio(recorte) if isinstance(recorte, str) else recorte
    if ratio:
        img = recortar(img, ratio)
    img = redimensionar(img, ancho)
    salida = Image.fromarray(img)
    if marca or logo:
        salida = marca_de_agua(salida, texto=marca, logo=logo)
    return salida


def guardar(pil_img, destino, calidad=92):
    """Guarda en JPG con perfil de color estandar. No copia los datos EXIF
    (asi no se filtra la ubicacion GPS de las fotos de los clientes)."""
    pil_img.save(destino, "JPEG", quality=calidad, optimize=True, progressive=True,
                 subsampling=0 if calidad >= 90 else 2)
    return destino
