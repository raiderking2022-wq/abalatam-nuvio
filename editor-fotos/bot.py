#!/usr/bin/env python3
"""Bot de Telegram: el cliente envia fotos, elige un estilo y recibe las fotos editadas.

Variables de entorno (ver .env.example):
    TELEGRAM_TOKEN     token de @BotFather (obligatorio)
    ADMIN_IDS          IDs de Telegram de los administradores, separados por coma
    FOTOS_GRATIS_DIA   fotos gratis por dia y usuario (por defecto 5)
    MARCA_GRATIS       marca de agua de las fotos gratis (vacio = sin marca)
    TEXTO_PRECIOS      precios que ve el cliente en /comprar
    TEXTO_PAGO         como pagar (transferencia, PayPal...)
    BOT_DB             archivo de la base de datos (por defecto bot.db)
"""
import asyncio
import logging
import os
import sqlite3
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, Update)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import motor


def cargar_env(ruta=Path(__file__).with_name(".env")):
    """Lee el archivo .env (CLAVE=valor). Las variables ya definidas tienen prioridad."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


cargar_env()

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
GRATIS_DIA = int(os.environ.get("FOTOS_GRATIS_DIA", "5"))
MARCA_GRATIS = os.environ.get("MARCA_GRATIS", "Editado con FotoBot")
MAX_LOTE = 30          # fotos maximas por lote
MAX_MB = 20            # Telegram no deja a los bots descargar archivos de mas de 20 MB
TEXTO_PRECIOS = os.environ.get("TEXTO_PRECIOS", (
    "• 50 fotos: RD$300\n"
    "• 200 fotos: RD$1,000\n"
    "• 500 fotos: RD$2,000"))
TEXTO_PAGO = os.environ.get("TEXTO_PAGO", (
    "Transferencia o PayPal. Envia el comprobante al administrador junto con tu ID."))
# En el archivo .env los saltos de linea se escriben como \n
TEXTO_PRECIOS = TEXTO_PRECIOS.replace("\\n", "\n")
TEXTO_PAGO = TEXTO_PAGO.replace("\\n", "\n")


# ================================================================ base de datos

class Datos:
    """SQLite: usuarios, creditos comprados, preferencias y uso diario gratis."""

    def __init__(self, ruta):
        self.con = sqlite3.connect(ruta, check_same_thread=False)
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY, nombre TEXT, creditos INTEGER NOT NULL DEFAULT 0,
                marca TEXT, recorte TEXT, ancho INTEGER, editadas INTEGER NOT NULL DEFAULT 0,
                creado TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS uso_gratis (
                usuario INTEGER, fecha TEXT, cantidad INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (usuario, fecha));
            CREATE TABLE IF NOT EXISTS pagos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, usuario INTEGER, cantidad INTEGER,
                admin INTEGER, fecha TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        self.con.commit()

    def usuario(self, uid, nombre=None):
        self.con.execute("INSERT OR IGNORE INTO usuarios (id, nombre) VALUES (?, ?)", (uid, nombre))
        if nombre:
            self.con.execute("UPDATE usuarios SET nombre = ? WHERE id = ?", (nombre, uid))
        self.con.commit()
        fila = self.con.execute(
            "SELECT creditos, marca, recorte, ancho, editadas FROM usuarios WHERE id = ?", (uid,)).fetchone()
        return dict(zip(("creditos", "marca", "recorte", "ancho", "editadas"), fila))

    def existe(self, uid):
        return self.con.execute("SELECT 1 FROM usuarios WHERE id = ?", (uid,)).fetchone() is not None

    def preferencia(self, uid, campo, valor):
        assert campo in ("marca", "recorte", "ancho")
        self.con.execute(f"UPDATE usuarios SET {campo} = ? WHERE id = ?", (valor, uid))
        self.con.commit()

    def gratis_restantes(self, uid):
        fila = self.con.execute("SELECT cantidad FROM uso_gratis WHERE usuario = ? AND fecha = ?",
                                (uid, date.today().isoformat())).fetchone()
        return max(0, GRATIS_DIA - (fila[0] if fila else 0))

    def reservar(self, uid, pedidas):
        """Descuenta primero de las gratis del dia y luego de los creditos.
        Devuelve (gratis, pagadas). Todo en una transaccion."""
        with self.con:
            gratis = min(pedidas, self.gratis_restantes(uid))
            creditos = self.con.execute("SELECT creditos FROM usuarios WHERE id = ?", (uid,)).fetchone()[0]
            pagadas = min(pedidas - gratis, creditos)
            if gratis:
                self.con.execute(
                    "INSERT INTO uso_gratis (usuario, fecha, cantidad) VALUES (?, ?, ?) "
                    "ON CONFLICT(usuario, fecha) DO UPDATE SET cantidad = cantidad + excluded.cantidad",
                    (uid, date.today().isoformat(), gratis))
            if pagadas:
                self.con.execute("UPDATE usuarios SET creditos = creditos - ? WHERE id = ?", (pagadas, uid))
        return gratis, pagadas

    def devolver(self, uid, gratis, pagadas):
        """Si una foto falla, se le devuelve al cliente."""
        with self.con:
            if gratis:
                self.con.execute("UPDATE uso_gratis SET cantidad = MAX(0, cantidad - ?) "
                                 "WHERE usuario = ? AND fecha = ?", (gratis, uid, date.today().isoformat()))
            if pagadas:
                self.con.execute("UPDATE usuarios SET creditos = creditos + ? WHERE id = ?", (pagadas, uid))

    def sumar_editadas(self, uid, n):
        with self.con:
            self.con.execute("UPDATE usuarios SET editadas = editadas + ? WHERE id = ?", (n, uid))

    def agregar_creditos(self, uid, cantidad, admin):
        with self.con:
            self.con.execute("UPDATE usuarios SET creditos = MAX(0, creditos + ?) WHERE id = ?", (cantidad, uid))
            self.con.execute("INSERT INTO pagos (usuario, cantidad, admin) VALUES (?, ?, ?)",
                             (uid, cantidad, admin))
        return self.usuario(uid)["creditos"]

    def estadisticas(self):
        q = lambda sql: self.con.execute(sql).fetchone()[0] or 0
        return dict(
            usuarios=q("SELECT COUNT(*) FROM usuarios"),
            editadas=q("SELECT SUM(editadas) FROM usuarios"),
            hoy=q(f"SELECT SUM(cantidad) FROM uso_gratis WHERE fecha = '{date.today().isoformat()}'"),
            vendidas=q("SELECT SUM(cantidad) FROM pagos WHERE cantidad > 0"),
            compradores=q("SELECT COUNT(DISTINCT usuario) FROM pagos WHERE cantidad > 0"),
        )


DB = Datos(os.environ.get("BOT_DB", "bot.db"))


# ================================================================ teclados y textos

def teclado_estilos(n):
    botones = [InlineKeyboardButton(nombre.replace("_", " ").capitalize(), callback_data=f"estilo:{nombre}")
               for nombre in motor.ESTILOS]
    filas = [botones[i:i + 2] for i in range(0, len(botones), 2)]
    filas.append([InlineKeyboardButton("👀 Ver estilos", callback_data="info"),
                  InlineKeyboardButton("🗑 Cancelar", callback_data="cancelar")])
    return InlineKeyboardMarkup(filas)


def texto_lote(n):
    return (f"📷 Tienes *{n}* foto{'s' if n != 1 else ''} en cola.\n"
            "Envia mas o elige un estilo para editarlas:")


def texto_saldo(uid):
    u = DB.usuario(uid)
    ajustes = []
    if u["marca"]:
        ajustes.append(f"marca «{escape_markdown(u['marca'])}»")
    if u["recorte"]:
        ajustes.append(f"recorte {u['recorte']}")
    if u["ancho"]:
        ajustes.append(f"ancho {u['ancho']} px")
    return (f"🎁 Gratis hoy: *{DB.gratis_restantes(uid)}* de {GRATIS_DIA}\n"
            f"💳 Creditos comprados: *{u['creditos']}*\n"
            f"✅ Fotos editadas en total: {u['editadas']}\n"
            f"⚙️ Ajustes: {', '.join(ajustes) if ajustes else 'ninguno'}")


# ================================================================ comandos

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    DB.usuario(u.id, u.full_name)
    await update.message.reply_text(
        f"¡Hola, {u.first_name}! 👋\n\n"
        "Edito tus fotos de forma automatica: luz, color, nitidez, recorte y marca de agua.\n\n"
        "*Como usarlo*\n"
        "1. Enviame una o varias fotos (hasta 30 a la vez).\n"
        "2. Elige un estilo.\n"
        "3. Te las devuelvo editadas en alta calidad.\n\n"
        "💡 Para la mejor calidad, envialas *como archivo* (📎 → Archivo). "
        "Asi Telegram no las comprime.\n\n"
        f"Tienes *{GRATIS_DIA} fotos gratis cada dia*. Escribe /ayuda para ver todo lo que puedo hacer.",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Comandos*\n"
        "/saldo – fotos gratis y creditos\n"
        "/comprar – precios y como pagar\n"
        "/estilos – que hace cada estilo\n"
        "/marca Mi Estudio – marca de agua en tus fotos (/marca no para quitarla)\n"
        "/recorte 4:5 – recortar (1:1, 4:5, 16:9… o /recorte no)\n"
        "/ancho 2000 – ancho maximo en pixeles (/ancho no)\n"
        "/cancelar – vaciar las fotos en cola\n\n"
        "Formatos: JPG, PNG, WebP, HEIC (iPhone) y RAW de camara (como archivo).",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_estilos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lineas = [f"• *{n.replace('_', ' ').capitalize()}*: {d}" for n, d in motor.DESCRIPCIONES.items()]
    await update.effective_message.reply_text("\n".join(lineas), parse_mode=ParseMode.MARKDOWN)


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    DB.usuario(u.id, u.full_name)
    await update.message.reply_text(texto_saldo(u.id), parse_mode=ParseMode.MARKDOWN)


async def cmd_comprar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"💳 *Paquetes de fotos*\n{TEXTO_PRECIOS}\n\n*Como pagar*\n{TEXTO_PAGO}\n\n"
        f"Tu ID es: `{uid}`\nCuando se confirme el pago, los creditos aparecen en /saldo.",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_preferencia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/marca, /recorte y /ancho comparten esta funcion."""
    campo = update.message.text.split()[0].lstrip("/").split("@")[0]
    uid = update.effective_user.id
    DB.usuario(uid, update.effective_user.full_name)
    valor = " ".join(context.args).strip()
    ejemplos = {"marca": "/marca Estudio Lopez", "recorte": "/recorte 4:5", "ancho": "/ancho 2000"}
    if not valor:
        actual = DB.usuario(uid)[campo]
        await update.message.reply_text(f"Actual: {actual or 'ninguno'}\nEjemplo: {ejemplos[campo]}  (o «no» para quitar)")
        return
    if valor.lower() in ("no", "ninguno", "quitar"):
        DB.preferencia(uid, campo, None)
        await update.message.reply_text(f"Listo, {campo} desactivado.")
        return
    try:
        if campo == "recorte":
            motor.leer_ratio(valor)
        elif campo == "ancho":
            valor = int(valor)
            if not 200 <= valor <= 10000:
                raise ValueError
        elif len(valor) > 40:
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"No entendi. Ejemplo: {ejemplos[campo]}")
        return
    DB.preferencia(uid, campo, valor)
    await update.message.reply_text(f"✅ {campo.capitalize()}: {valor}")


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("fotos", None)
    context.user_data.pop("menu", None)
    await update.effective_message.reply_text("🗑 Cola vaciada.")


# ---------------------------------------------------------------- administrador

def es_admin(update):
    return update.effective_user.id in ADMIN_IDS


async def cmd_agregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/agregar ID CANTIDAD  (cantidad negativa para corregir)"""
    if not es_admin(update):
        return
    try:
        uid, cantidad = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Uso: /agregar ID CANTIDAD")
        return
    if not DB.existe(uid):
        await update.message.reply_text("Ese usuario no ha iniciado el bot todavia (/start).")
        return
    total = DB.agregar_creditos(uid, cantidad, update.effective_user.id)
    await update.message.reply_text(f"✅ Usuario {uid}: {cantidad:+d} creditos. Ahora tiene {total}.")
    if cantidad > 0:
        try:
            await context.bot.send_message(uid, f"🎉 ¡Pago confirmado! Se agregaron {cantidad} creditos. "
                                                 f"Ahora tienes {total}.")
        except Exception as e:
            log.warning("no se pudo avisar a %s: %s", uid, e)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update):
        return
    s = DB.estadisticas()
    await update.message.reply_text(
        f"👥 Usuarios: {s['usuarios']}\n📸 Fotos editadas: {s['editadas']}\n"
        f"🎁 Gratis usadas hoy: {s['hoy']}\n💳 Creditos vendidos: {s['vendidas']} "
        f"({s['compradores']} clientes)")


# ================================================================ recibir fotos

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    DB.usuario(update.effective_user.id, update.effective_user.full_name)

    if msg.photo:
        archivo = msg.photo[-1]          # la version mas grande
        nombre = f"foto_{archivo.file_unique_id}.jpg"
        tam = archivo.file_size or 0
    else:
        archivo = msg.document
        nombre = archivo.file_name or f"foto_{archivo.file_unique_id}.jpg"
        tam = archivo.file_size or 0
        if Path(nombre).suffix.lower() not in motor.extensiones_soportadas():
            await msg.reply_text("Ese archivo no es una foto que pueda editar. /ayuda muestra los formatos.")
            return

    if tam > MAX_MB * 1024 * 1024:
        await msg.reply_text(f"«{nombre}» pesa mas de {MAX_MB} MB (limite de Telegram para bots). "
                             "Exportala a JPG y enviala de nuevo.")
        return

    fotos = context.user_data.setdefault("fotos", [])
    if len(fotos) >= MAX_LOTE:
        await msg.reply_text(f"Maximo {MAX_LOTE} fotos por lote. Elige un estilo para estas primero.")
        return
    fotos.append((archivo.file_id, nombre))

    # Un solo menu que se actualiza, aunque lleguen 20 fotos de un album.
    menu = context.user_data.get("menu")
    if menu:
        try:
            await context.bot.edit_message_text(texto_lote(len(fotos)), chat_id=menu[0], message_id=menu[1],
                                                reply_markup=teclado_estilos(len(fotos)),
                                                parse_mode=ParseMode.MARKDOWN)
            return
        except Exception:
            pass  # el mensaje es viejo o igual; se envia uno nuevo
    nuevo = await msg.reply_text(texto_lote(len(fotos)), reply_markup=teclado_estilos(len(fotos)),
                                 parse_mode=ParseMode.MARKDOWN)
    context.user_data["menu"] = (nuevo.chat_id, nuevo.message_id)


# ================================================================ editar

def _editar_archivo(ruta, estilo, ajustes, marca, con_comparacion):
    """Trabajo pesado (corre en un hilo aparte para no congelar el bot)."""
    original = motor.cargar(ruta)
    editada = motor.editar(original, estilo, ajustes["recorte"], ajustes["ancho"], marca)
    salida = BytesIO()
    motor.guardar(editada, salida)
    comp = None
    if con_comparacion:
        comp = BytesIO()
        motor.guardar(motor.antes_despues(original, editada), comp, 85)
    return salida.getvalue(), (comp.getvalue() if comp else None)


async def boton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancelar":
        context.user_data.pop("fotos", None)
        context.user_data.pop("menu", None)
        await q.edit_message_text("🗑 Cola vaciada.")
        return
    if q.data == "info":
        await cmd_estilos(update, context)
        return

    estilo = q.data.split(":", 1)[1]
    if estilo not in motor.ESTILOS:
        return
    fotos = context.user_data.pop("fotos", [])
    context.user_data.pop("menu", None)
    if not fotos:
        await q.edit_message_text("No hay fotos en cola. Enviame alguna 📷")
        return

    uid = update.effective_user.id
    ajustes = DB.usuario(uid)
    gratis, pagadas = DB.reservar(uid, len(fotos))
    a_editar = gratis + pagadas
    if a_editar == 0:
        await q.edit_message_text("Se acabaron tus fotos gratis de hoy y no tienes creditos. 😕\n"
                                  "Escribe /comprar para ver los paquetes, o vuelve manana.")
        return

    nombre_estilo = estilo.replace("_", " ")
    await q.edit_message_text(f"⏳ Editando {a_editar} foto{'s' if a_editar != 1 else ''} "
                              f"con estilo *{nombre_estilo}*…", parse_mode=ParseMode.MARKDOWN)

    resultados, fallidas = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (file_id, nombre) in enumerate(fotos[:a_editar]):
            # Las gratis llevan la marca del bot; las pagadas, la del cliente (si puso una).
            marca = (MARCA_GRATIS or None) if i < gratis else ajustes["marca"]
            try:
                ruta = Path(tmp) / f"{i}{Path(nombre).suffix.lower() or '.jpg'}"
                await (await context.bot.get_file(file_id)).download_to_drive(ruta)
                datos, comp = await asyncio.to_thread(_editar_archivo, ruta, estilo, ajustes, marca, i == 0)
                resultados.append((Path(nombre).stem + "_editada.jpg", datos))
                if comp:
                    await q.message.reply_photo(comp, caption="Antes / después")
            except Exception as e:
                log.exception("error editando %s", nombre)
                fallidas.append((i, nombre, str(e)))

    # Devolver lo que fallo (gratis o pagado segun su posicion).
    if fallidas:
        g = sum(1 for i, *_ in fallidas if i < gratis)
        DB.devolver(uid, g, len(fallidas) - g)

    # Enviar como documentos (sin compresion) en grupos de 10.
    for k in range(0, len(resultados), 10):
        grupo = [InputMediaDocument(BytesIO(d), filename=n) for n, d in resultados[k:k + 10]]
        if len(grupo) == 1:
            await q.message.reply_document(grupo[0].media, filename=resultados[k][0])
        else:
            await q.message.reply_media_group(grupo)

    DB.sumar_editadas(uid, len(resultados))
    resumen = [f"✅ {len(resultados)} foto{'s' if len(resultados) != 1 else ''} lista{'s' if len(resultados) != 1 else ''}."]
    if fallidas:
        resumen.append(f"⚠️ {len(fallidas)} no se pudieron editar (no se cobraron): "
                       + ", ".join(escape_markdown(n) for _, n, _ in fallidas))
    sobrantes = len(fotos) - a_editar
    if sobrantes:
        resumen.append(f"⏸ {sobrantes} quedaron sin editar porque no te alcanzo el saldo. /comprar")
    if gratis and MARCA_GRATIS:
        resumen.append(f"ℹ️ Las fotos gratis llevan la marca «{escape_markdown(MARCA_GRATIS)}». Con creditos salen sin ella.")
    resumen.append("")
    resumen.append(texto_saldo(uid))
    await q.message.reply_text("\n".join(resumen), parse_mode=ParseMode.MARKDOWN)


async def otro_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enviame fotos para editarlas 📷  o escribe /ayuda.")


async def error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("error no controlado", exc_info=context.error)


# ================================================================ arranque

def crear_app(token):
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["ayuda", "help"], cmd_ayuda))
    app.add_handler(CommandHandler("estilos", cmd_estilos))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("comprar", cmd_comprar))
    app.add_handler(CommandHandler(["marca", "recorte", "ancho"], cmd_preferencia))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("agregar", cmd_agregar))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, recibir_foto))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, otro_texto))
    app.add_handler(CallbackQueryHandler(boton))
    app.add_error_handler(error)
    return app


def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Falta la variable de entorno TELEGRAM_TOKEN (pidela a @BotFather).")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS esta vacio: nadie podra usar /agregar ni /stats.")
    log.info("Bot iniciado. HEIC: %s, RAW: %s", motor.HEIC, bool(motor.rawpy))
    crear_app(token).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
