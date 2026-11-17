# Editor de fotos por lote + bot de Telegram

Edita muchas fotos de forma automática: luz, color, balance de blancos, nitidez, recorte, tamaño y marca de agua.
Se puede usar de dos formas:

- **Consola** (`editor_fotos.py`): procesa una carpeta completa en tu computadora.
- **Bot de Telegram** (`bot.py`): el cliente envía fotos, elige un estilo y las recibe editadas. Incluye fotos gratis por día, créditos de pago y un panel de administrador.

Las dos usan el mismo motor (`motor.py`).

## Qué mejora respecto a la primera versión

| Antes | Ahora |
|---|---|
| El brillo se multiplicaba y quemaba los blancos | Curva gamma más estiramiento de niveles: aclara sin quemar |
| El color se ajustaba en RGB (subir la luz lavaba los colores) | Se ajusta en LAB: la luz y el color van por separado |
| La saturación subía todo por igual (piel anaranjada) | Tipo *vibrance*: sube más los colores apagados |
| La nitidez creaba bordes de colores | Se aplica solo a la luz, con un radio según el tamaño |
| No corregía la luz amarilla de los bombillos | Balance de blancos automático que mide solo en zonas neutras |
| No recuperaba las ventanas en interiores | Detalle local (CLAHE), muy útil en inmobiliaria |
| Se perdía calidad en cada paso (8 bits) | Todo en decimales; se convierte una sola vez al final |
| `foto.png` y `foto.jpg` se sobrescribían | Nombres únicos (`foto_2.jpg`) |
| PNG transparente quedaba con fondo negro | Fondo blanco |
| Una foto a la vez | Usa todos los núcleos del procesador |
| Solo JPG/PNG/WebP | También HEIC (iPhone), TIFF y RAW (CR2, NEF, ARW, DNG…) |
| Marca de agua solo con texto | Texto con sombra (se lee sobre fondos claros) o logo PNG |
| — | Imagen **antes/después** para mostrar al cliente |
| Guardaba el GPS de las fotos | No copia EXIF: no se filtra la ubicación de los clientes |

**Estilos:** natural, calido, frio, vivido, inmobiliaria, producto, boda, blanco_negro.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso por consola

```bash
python3 editor_fotos.py ENTRADA SALIDA --estilo inmobiliaria --marca "Mi Estudio" --recorte 4:5 --ancho 2000 --comparar
python3 editor_fotos.py --estilos          # lista de estilos
```

Opciones: `--logo logo.png` (en vez de texto), `--calidad 85`, `--procesos 2`.
Con `--comparar` se crea `SALIDA/comparaciones/` con imágenes antes/después.

Para ajustar un estilo, cambia sus números en `ESTILOS` dentro de `motor.py` (cada parámetro está explicado ahí).

## Bot de Telegram

1. En Telegram, habla con **@BotFather** → `/newbot` → copia el token.
2. Habla con **@userinfobot** para saber tu ID.
3. Copia `.env.example` como `.env` y llena `TELEGRAM_TOKEN`, `ADMIN_IDS`, los precios y la forma de pago.
4. Arranca el bot: `python3 bot.py`

### Qué ve el cliente

- Envía una o varias fotos (hasta 30). Si las manda **como archivo**, conservan la calidad completa.
- Aparece un menú con los estilos. Al elegir uno, recibe primero una imagen **antes/después** y luego las fotos editadas como archivos.
- `/saldo`, `/comprar`, `/estilos`, `/marca Mi Estudio`, `/recorte 4:5`, `/ancho 2000`, `/cancelar`.

### Cómo se cobra

- Cada usuario tiene `FOTOS_GRATIS_DIA` fotos gratis al día, con la marca `MARCA_GRATIS` (publicidad gratis para ti).
- Las fotos con créditos salen **sin** tu marca, con la marca del cliente si configuró una.
- El cliente paga por transferencia o PayPal y te envía el comprobante con su ID (lo ve en `/comprar`).
- Tú escribes `/agregar ID 200` y al cliente le llega el aviso. Con un número negativo corriges un error.
- `/stats` muestra usuarios, fotos editadas y créditos vendidos.
- Si una foto falla, no se cobra.

## Despliegue barato

El bot usa *polling*, así que no necesita dominio ni HTTPS. Funciona en cualquier lugar donde pueda quedarse encendido:

- **Un VPS de US$4–6 al mes** (Hetzner, DigitalOcean, Contabo). Es lo más estable. Con Docker:
  ```bash
  docker build -t fotobot .
  docker run -d --restart=always --env-file .env -v fotobot-datos:/data --name fotobot fotobot
  ```
- **Railway o Render** (tienen plan gratis o muy barato): sube esta carpeta, pon las variables del `.env` en su panel y agrega un volumen en `/data` para no perder los créditos.
- **Una PC vieja o una Raspberry Pi** en casa: `python3 bot.py` dentro de `tmux` o como servicio de systemd.

Importante: el archivo `bot.db` guarda los créditos de los clientes. Haz copias de seguridad.

## Pruebas

```bash
python3 -m pytest -q
```

## Siguientes pasos sugeridos

1. Probar con 20–30 fotos reales de cada tipo de cliente (bodas, casas, productos) y afinar los números de `ESTILOS`.
2. Ofrecer el bot gratis a 3–5 fotógrafos o inmobiliarias a cambio de su opinión y de las fotos antes/después para tu publicidad.
3. Cuando haya clientes que pagan: cobrar con enlaces de pago (PayPal o Stripe) que sumen créditos automáticamente, y después hacer la página web usando el mismo `motor.py`.
