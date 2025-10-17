from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup,Bot
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, ContextTypes,
    CommandHandler, ConversationHandler, MessageHandler, filters
)
import csv
import logging
import os 
import uuid
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
from telegram.error import BadRequest
import re  # ya importado en el archivo; si no, esta línea es segura

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- Configuración y Almacenamiento de Datos ---
# Cargar .env simple (opcional) sin depender de python-dotenv
def _load_dotenv_simple(path: str = '.env'):
    """Carga variables KEY=VALUE desde .env hacia os.environ sin sobrescribir las existentes."""
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except Exception as e:
        logging.warning(f"No se pudo leer {path}: {e}")

# Intentar cargar .env local (si existe)
_load_dotenv_simple()

# Leer TOKEN desde variable de entorno
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("Falta TELEGRAM_TOKEN en variables de entorno. Define TELEGRAM_TOKEN.")

# Leer ADMIN_ID desde variable de entorno y convertir a int
_admin_id_raw = os.getenv("ADMIN_ID")
if not _admin_id_raw:
    raise RuntimeError("Falta ADMIN_ID en variables de entorno. Define ADMIN_ID.")
try:
    ADMIN_ID = int(_admin_id_raw)
except ValueError:
    raise RuntimeError("ADMIN_ID debe ser un número entero. Revisa la variable de entorno ADMIN_ID.")

# Archivos de persistencia
CSV_CLIENTES = 'clientes.csv'
STOCK_FILE = 'stock.csv'
COMPRAS_FILE = 'compras_global.csv' 
# Cambiado para persistir combos en CSV compatible con Excel
COMBOS_FILE = 'combos.csv'
# Añade estas variables de configuración (edítalas con tus datos)
ADMIN_WHATSAPP = "+529992779422"  # Ej: "+52 844 212 5550" — coloca tu número de WhatsApp aquí
BANK_ACCOUNT = "722969020048622836 💰 Stp / Mercado Pago 👤 Yobas Vnts"    # Ej: "Banco XYZ - CLABE: 012345678901234567" — coloca los datos bancarios aquí
MIN_RECARGA = 50.0   # Mínimo de recarga en pesos


# Variables globales para el estado
clientes = {}
tmp_venta = {} # Usado para /addventa
tmp_reporte = {} # Usado para el flujo de Reporte
ADMIN_USERNAME = "YobasAdmin" # Nombre de referencia
ADMIN_PHONE = ""  # Configura aquí tu número, ej: "+52 844 212 5550"
WELCOME_IMAGE = "welcome_bot.jpg"  # Coloca este archivo en el mismo directorio o cambia el nombre
# Constante para la garantía
GARANTIA_DIAS = 25 

# --- Estados de Conversación ---
AGREGAR_TIPO, AGREGAR_PERFILES, AGREGAR_CORREO, AGREGAR_PASS, AGREGAR_PRECIO = range(5)
REPORTE_CORREO, REPORTE_PASS, REPORTE_FECHA, REPORTE_ID_COMPRA, REPORTE_DESCRIPCION = range(5, 10)
AGREGAR_MATERIAL = 10

# Estados para el flujo de combos
ADD_COMBO_TITULO, ADD_COMBO_SUBNOMBRE, ADD_COMBO_PRECIO, ADD_COMBO_PLATAFORMAS = range(20, 24)
combos = []


# --- Funciones de Carga/Guardado del Administrador (Simplificadas) ---

def is_admin(user_id):
    """Verifica si el ID es el del administrador fijo."""
    return user_id == ADMIN_ID


# --- Carga y guardado de Clientes ---
def cargar_clientes():
    """Carga los saldos de los clientes desde el archivo CSV."""
    global clientes
    clientes = {}  # Reinicia para evitar acumulaciones
    try:
        with open(CSV_CLIENTES, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) == 2:
                    try:
                        clientes[int(row[0])] = float(row[1])
                    except ValueError as e:
                        logging.error(f"Error al parsear fila en {CSV_CLIENTES}: {row}. Error: {e}")
        logging.info(f"Clientes cargados: {len(clientes)}")
    except FileNotFoundError:
        logging.warning(f"{CSV_CLIENTES} no existe. Se creará al guardar.")
    except Exception as e:
        logging.error(f"Error desconocido al cargar clientes: {e}")

def guardar_clientes():
    """Guarda los saldos actuales de los clientes en el archivo CSV."""
    with open(CSV_CLIENTES, 'w', newline='') as f:
        writer = csv.writer(f)
        for user, saldo in clientes.items():
            writer.writerow([user, f"{saldo:.2f}"])

def inicializar_usuario(user_id):
    """Inicializa un usuario con saldo 0 si no existe."""
    if user_id not in clientes:
        clientes[user_id] = 0.0
        guardar_clientes()
        logging.info(f"Nuevo usuario inicializado: {user_id}")


# --- Lógica de Stock y Precios Dinámicos ---

def save_combos_csv():
    """Guarda la lista `combos` en `COMBOS_FILE` (CSV). Plataformas separadas por '|'."""
    try:
        with open(COMBOS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Cabecera para que Excel lo abra bien
            writer.writerow(['titulo', 'subnombre', 'precio', 'plataformas'])
            for c in combos:
                titulo = c.get('titulo', '')
                sub = c.get('subnombre', '')
                precio = float(c.get('precio', 0.0))
                plataformas = c.get('plataformas', []) or []
                # Reemplazar '|' dentro de nombres por espacio para evitar colisiones
                plataformas_str = '|'.join([p.replace('|', ' ') for p in plataformas])
                writer.writerow([titulo, sub, f"{precio:.2f}", plataformas_str])
    except Exception as e:
        logging.exception(f"Error guardando {COMBOS_FILE}: {e}")

def load_combos_csv():
    """Carga `combos` desde COMBOS_FILE si existe. Rellena la lista global `combos`."""
    global combos
    combos = []
    try:
        if not os.path.exists(COMBOS_FILE):
            return
        with open(COMBOS_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    titulo = (row.get('titulo') or '').strip()
                    sub = (row.get('subnombre') or '').strip()
                    precio = float((row.get('precio') or '0').strip() or 0)
                    plataformas_str = (row.get('plataformas') or '').strip()
                    plataformas = [p for p in plataformas_str.split('|') if p]
                    combos.append({
                        'titulo': titulo,
                        'subnombre': sub,
                        'precio': precio,
                        'plataformas': plataformas
                    })
                except Exception as e:
                    logging.exception(f"Fila combos inválida en {COMBOS_FILE}: {row} - {e}")
    except Exception as e:
        logging.exception(f"Error cargando {COMBOS_FILE}: {e}")

def load_stock():
    """Carga todo el stock desde el archivo CSV, sin filtrar por número de campos.
    Devuelve una lista de filas (cada fila es una lista de strings)."""
    stock_data = []
    try:
        with open(STOCK_FILE, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                # Normalizar espacios en cada campo
                if row:
                    stock_data.append([c.strip() for c in row])
    except FileNotFoundError:
        logging.warning(f"{STOCK_FILE} no existe.")
    except Exception as e:
        logging.error(f"Error al cargar stock: {e}")
    return stock_data

def save_stock(stock_list):
    """Sobreescribe el archivo de stock con la lista actual."""
    try:
        with open(STOCK_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(stock_list)
        logging.info("Stock guardado después de la eliminación.")
    except Exception as e:
        logging.error(f"Error al guardar stock: {e}")

def cleanup_stock():
    """Limpia entradas de stock inválidas o con perfiles a 0 y guarda si hay cambios."""
    cuentas = load_stock()
    changed = False
    cleaned = []
    for row in cuentas:
        if not row or len(row) < 5:
            # ignorar filas malformadas
            continue
        # Si es registro con perfiles (>=7 campos), validar perfiles_disponibles
        if len(row) >= 7:
            try:
                perfiles_disponibles = int(row[5])
            except (ValueError, TypeError):
                # fila corrupta -> eliminar
                changed = True
                continue
            if perfiles_disponibles <= 0:
                # eliminar fila si no tiene perfiles disponibles
                changed = True
                continue
        # Caso normal: mantener fila
        cleaned.append(row)
    if changed:
        save_stock(cleaned)
    return cleaned

def get_dynamic_stock_info():
    """
    Analiza el stock y devuelve un diccionario con los precios mínimos
    agrupados por categoria (completa/perfil) -> plataforma.
    Tolerante a filas con 5 o 7 campos.
    """
    stock_list = load_stock()
    
    stock_info = defaultdict(lambda: defaultdict(lambda: {'precio': float('inf'), 'tipos_disponibles': set()}))

    for row in stock_list:
        if len(row) < 2:
            continue
        platform = row[0]
        tipo = row[1]
        # precio normalmente en índice 4; si no existe, tomar el último campo
        precio_str = row[4] if len(row) > 4 else row[-1]
        try:
            precio = float(precio_str)
        except (ValueError, TypeError):
            logging.error(f"Precio inválido encontrado: {precio_str} para {platform} ({tipo})")
            continue
        
        tipo_lower = tipo.lower()
        plataforma_clave = platform.strip()
        
        categoria = 'otro'
        if 'perfil' in tipo_lower and 'completa' not in tipo_lower:
            categoria = 'perfil'
        elif 'completa' in tipo_lower and 'perfil' not in tipo_lower:
            categoria = 'completa'
        else:
            # Lógica de respaldo
            if tipo_lower.startswith(('1 perfil', 'perfil')):
                categoria = 'perfil'
            elif tipo_lower.startswith(('cuenta', 'full', 'premium', 'basico', 'estandar', 'completa')):
                categoria = 'completa'
        
        if categoria in ['completa', 'perfil']:
            if precio < stock_info[categoria][plataforma_clave]['precio']:
                 stock_info[categoria][plataforma_clave]['precio'] = precio
            stock_info[categoria][plataforma_clave]['tipos_disponibles'].add(tipo.strip())
        
    return stock_info


# --- Flujo para agregar cuentas (Conversación de Admin) - CON VALIDACIONES ---

async def addventa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        msg = "❌ Solo el administrador puede agregar ventas."
        try:
            await update.message.reply_text(msg)
        except Exception:
            logging.exception("addventa: fallo reply_text para no-admin")
            try:
                await context.bot.send_message(chat_id=user_id, text=msg)
            except Exception:
                logging.exception("addventa: fallo fallback send_message para no-admin")
        return ConversationHandler.END

    if not context.args:
        msg = "❌ Uso: /addventa <nombre de la plataforma>\nEj: /addventa Netflix"
        try:
            await update.message.reply_text(msg)
        except Exception:
            logging.exception("addventa: fallo reply_text en uso")
            try:
                await context.bot.send_message(chat_id=user_id, text=msg)
            except Exception:
                logging.exception("addventa: fallo fallback send_message en uso")
        return ConversationHandler.END

    Plataforma = " ".join(context.args).strip()
    if not Plataforma:
        msg = "❌ El nombre de la plataforma no puede estar vacío."
        try:
            await update.message.reply_text(msg)
        except Exception:
            logging.exception("addventa: fallo reply_text plataforma vacía")
            try:
                await context.bot.send_message(chat_id=user_id, text=msg)
            except Exception:
                logging.exception("addventa: fallo fallback send_message plataforma vacía")
        return ConversationHandler.END

    tmp_venta[user_id] = {"Plataforma": Plataforma}
    inicio_msg = f"Añadiendo {Plataforma}.\nResponde con el tipo de cuenta (Ej: 'Completa', 'Perfil 1'):"
    try:
        await update.message.reply_text(inicio_msg, parse_mode="Markdown")
    except Exception:
        logging.exception("addventa: fallo reply_text en inicio del flujo")
        try:
            await context.bot.send_message(chat_id=user_id, text=inicio_msg, parse_mode="Markdown")
        except Exception:
            logging.exception("addventa: fallo fallback send_message en inicio del flujo — abortando flujo")
            return ConversationHandler.END

    return AGREGAR_TIPO

async def venta_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1: Recibe y valida el tipo de cuenta (Completa/Perfil)."""
    user_id = update.message.from_user.id
    # Validación: existe tmp_venta para este usuario?
    if user_id not in tmp_venta:
        await update.message.reply_text("❌ No hay ningún flujo de /addventa activo. Inicia con: /addventa <Plataforma>")
        return ConversationHandler.END

    tipo = update.message.text.strip()
    if not tipo:
        await update.message.reply_text("❌ El tipo de cuenta no puede estar vacío. Intenta nuevamente:")
        return AGREGAR_TIPO
    
    tipo_lower = tipo.lower()
    
    # Si contiene 'perfil' y NO 'completa', ir a preguntar perfiles
    if 'perfil' in tipo_lower and 'completa' not in tipo_lower and 'perfil' not in tmp_venta[user_id].get('Plataforma', '').lower():
        tmp_venta[user_id]['tipo_base'] = tipo
        await update.message.reply_text("¿Cuántos perfiles tiene esta cuenta (solo el número)?")
        return AGREGAR_PERFILES
    else:
        tmp_venta[user_id]['tipo'] = tipo
        await update.message.reply_text("Ingresa el correo de la cuenta:")
        return AGREGAR_CORREO

async def recargar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/recargar <ID_USUARIO> <monto> (Admin) o Instrucciones (Usuario)."""
    user_id = update.message.from_user.id

    # Usuario normal -> instrucciones de recarga con WhatsApp, cuenta y su ID
    if not is_admin(user_id):
        inicializar_usuario(user_id)
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])

        whatsapp = ADMIN_WHATSAPP or "(no configurado)"
        bank = BANK_ACCOUNT or "(no configurada)"
        min_text = f"${MIN_RECARGA:.2f}"

        texto = (
            f"💰 Tu saldo actual es: ${clientes.get(user_id, 0):.2f}\n\n"
            "Para recargar realiza una transferencia o depósito y envía el comprobante por WhatsApp.\n\n"
            f"📲 WhatsApp (envía comprobante): {whatsapp}\n"
            f"🏦 Cuenta / Referencia: {bank}\n\n"
            f"🔎 Tu ID de cliente (indícalo en el comprobante/WhatsApp): `{user_id}`\n"
            f"⚠️ Mínimo de recarga: {min_text} pesos.\n\n"
            "Después de enviar el comprobante por WhatsApp con tu ID de cliente, procesaremos la recarga."
        )

        await update.message.reply_text(
            texto,
            reply_markup=back_keyboard,
            parse_mode="Markdown"
        )
        return

    # Lógica de simulación de recarga para el ADMIN
    if not context.args or len(context.args) != 2:
        await update.message.reply_text("❌ Uso Admin: /recargar <ID_USUARIO> <monto>")
        return

    try:
        target_id = int(context.args[0])
        monto = float(context.args[1])

        if monto <= 0:
            await update.message.reply_text("❌ El monto debe ser positivo.")
            return

        inicializar_usuario(target_id)
        clientes[target_id] += monto
        guardar_clientes()

        await update.message.reply_text(f"✅ Recarga exitosa a ID {target_id} de ${monto:.2f}. Saldo actual: ${clientes[target_id]:.2f}")

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"🎉 Tu saldo ha sido recargado con ${monto:.2f} por el administrador. Saldo actual: ${clientes[target_id]:.2f}",
                parse_mode="Markdown"
            )
        except Exception:
            logging.warning(f"No se pudo enviar notificación al usuario {target_id}.")

    except ValueError:
        await update.message.reply_text("❌ ID de usuario o Monto inválido. Ambos deben ser números (el monto puede ser decimal).")

async def venta_perfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1.5: Recibe el número de perfiles si se seleccionó 'Perfil'."""
    user_id = update.message.from_user.id
    if user_id not in tmp_venta:
        await update.message.reply_text("❌ No hay ningún flujo de /addventa activo. Inicia con: /addventa <Plataforma>")
        return ConversationHandler.END

    try:
        num_perfiles = int(update.message.text.strip())
        if num_perfiles <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Ingresa un número de perfiles válido (ej: 1, 4, 5).")
        return AGREGAR_PERFILES
    
    base_tipo = tmp_venta[user_id].get('tipo_base', 'Perfil')
    tmp_venta[user_id]['tipo'] = f"{base_tipo} ({num_perfiles})"
    
    await update.message.reply_text("Ingresa el correo de la cuenta:")
    return AGREGAR_CORREO

async def show_recarga_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la información de recarga (WhatsApp, cuenta, ID del cliente y mínimo)."""
    query = getattr(update, "callback_query", None)
    # soportar llamadas tanto desde callback_query como desde message
    if query:
        try:
            await query.answer()
        except Exception:
            pass
        user_id = query.from_user.id
    else:
        # fallback: si se llama por mensaje directo
        user_id = update.effective_user.id if update.effective_user else (update.message.from_user.id if update.message else None)
        if not user_id:
            return

    whatsapp = ADMIN_WHATSAPP or "(no configurado)"
    bank = BANK_ACCOUNT or "(no configurada)"
    min_text = f"${MIN_RECARGA:.2f}"

    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])

    texto = (
        f"💰 Tu saldo actual es: ${clientes.get(user_id, 0):.2f}\n\n"
        "Para recargar realiza una transferencia o depósito y envía el comprobante por WhatsApp con tu ID de cliente.\n\n"
        f"📲 WhatsApp (envía comprobante): {whatsapp}\n"
        f"🏦 Cuenta / Referencia: {bank}\n\n"
        f"🔎 Tu ID de cliente (indícalo en el comprobante/WhatsApp): `{user_id}`\n"
        f"⚠️ Mínimo de recarga: {min_text} pesos.\n\n"
        "Envía el comprobante al WhatsApp indicado junto con tu ID de cliente para que procesemos la recarga."
    )

    try:
        if query:
            await query.edit_message_text(texto, reply_markup=back_keyboard, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=texto, reply_markup=back_keyboard, parse_mode="Markdown")
    except Exception:
        # fallback por si la edición falla
        await context.bot.send_message(chat_id=user_id, text=texto, reply_markup=back_keyboard, parse_mode="Markdown")

async def venta_correo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 2: Recibe y valida el correo."""
    user_id = update.message.from_user.id
    if user_id not in tmp_venta:
        await update.message.reply_text("❌ No hay ningún flujo de /addventa activo. Inicia con: /addventa <Plataforma>")
        return ConversationHandler.END

    correo = update.message.text.strip()
    if not correo:
        await update.message.reply_text("❌ El correo de la cuenta no puede estar vacío. Intenta nuevamente:")
        return AGREGAR_CORREO
        
    tmp_venta[user_id]['correo'] = correo
    await update.message.reply_text("Ingresa la contraseña de la cuenta:")
    return AGREGAR_PASS

async def venta_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 3: Recibe y valida la contraseña."""
    user_id = update.message.from_user.id
    if user_id not in tmp_venta:
        await update.message.reply_text("❌ No hay ningún flujo de /addventa activo. Inicia con: /addventa <Plataforma>")
        return ConversationHandler.END

    password = update.message.text.strip()
    if not password:
        await update.message.reply_text("❌ La contraseña no puede estar vacía. Intenta nuevamente:")
        return AGREGAR_PASS
        
    tmp_venta[user_id]['pass'] = password
    await update.message.reply_text("Ingresa el precio de venta de la cuenta (solo el número, ej: 50.50):")
    return AGREGAR_PRECIO

async def venta_precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 4: Recibe y valida el precio. Guarda la cuenta en stock.csv."""
    user_id = update.message.from_user.id
    if user_id not in tmp_venta:
        await update.message.reply_text("❌ No hay ningún flujo de /addventa activo. Inicia con: /addventa <Plataforma>")
        return ConversationHandler.END

    try:
        precio_text = update.message.text.strip().replace(',', '.')
        if not precio_text:
            await update.message.reply_text("❌ El precio no puede estar vacío. Intenta nuevamente:")
            return AGREGAR_PRECIO

        precio = float(precio_text)
        if precio <= 0:
            await update.message.reply_text("❌ El precio debe ser positivo. Intenta nuevamente:")
            return AGREGAR_PRECIO
    except ValueError:
        await update.message.reply_text("❌ El precio debe ser un número válido. Intenta nuevamente:")
        return AGREGAR_PRECIO

    tmp_venta[user_id]['precio'] = precio

    # Guardar en stock
    data = tmp_venta[user_id]
    tipo = data['tipo']
    perfiles = 1

    import re
    match = re.search(r'(\d+)', tipo)
    if 'perfil' in tipo.lower() and match:
        perfiles = int(match.group(1))
    else:
        perfiles = 1

    try:
        with open(STOCK_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([data['Plataforma'], tipo, data['correo'], data['pass'], f"{data['precio']:.2f}", perfiles, 1])
    except Exception as e:
        logging.exception(f"Error escribiendo en {STOCK_FILE}: {e}")
        await update.message.reply_text("❌ Error al guardar la cuenta en stock. Intenta de nuevo más tarde.")
        return ConversationHandler.END

    # Confirmación y fin del flujo (sin preguntar por material)
    await update.message.reply_text(
        f"✅ Se añadió una cuenta de {data['Plataforma']} ({tipo}) con {perfiles} perfil(es) disponibles. Precio: ${data['precio']:.2f}.\n\nRegistro completado.",
        parse_mode="Markdown"
    )

    # Limpiar tmp y terminar la conversación
    tmp_venta.pop(user_id, None)
    return ConversationHandler.END

    await update.message.reply_text(
        f"✅ Se añadió una cuenta de {data['Plataforma']} ({tipo}) con {perfiles} perfiles disponibles, precio ${data['precio']:.2f} cada uno.",
        parse_mode="Markdown"
    )
    # Preguntar por material adjunto
    await update.message.reply_text("¿Quieres agregar material adjunto (foto/documento) para esta cuenta? Envía el archivo o escribe 'no' para terminar.")
    return AGREGAR_MATERIAL

async def guardar_material_perfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    logging.info(f"guardar_material_perfil llamado por user_id={user_id}")
    data = tmp_venta.get(user_id)
    if not data:
        logging.warning("guardar_material_perfil: no hay tmp_venta para este usuario")
        await update.message.reply_text("❌ No hay registro activo. Usa /addventa de nuevo.")
        return ConversationHandler.END

    # Si el usuario escribe 'no' finalizamos el flujo
    text = (update.message.text or "").strip()
    if text and text.lower() == 'no':
        await update.message.reply_text("✅ Registro completado sin material.")
        tmp_venta.pop(user_id, None)
        return ConversationHandler.END

    correo = data.get('correo', 'unknown')
    tipo = data.get('tipo', '')
    import re
    match = re.search(r'(\d+)', tipo)
    perfiles = int(match.group(1)) if match else 1

    # Debug: registrar qué tiene el mensaje
    has_photo = bool(getattr(update.message, "photo", None))
    has_document = bool(getattr(update.message, "document", None))
    logging.info(f"guardar_material_perfil: has_photo={has_photo}, has_document={has_document}, text_present={bool(text)}")

    if not has_photo and not has_document:
        await update.message.reply_text("❌ Envía una foto o documento válido, o escribe 'no' para omitir.")
        return AGREGAR_MATERIAL

    # Guardar el archivo para cada perfil (si hay N perfiles, copiar el mismo archivo N veces)
    for i in range(1, perfiles + 1):
        try:
            if has_document:
                doc = update.message.document
                file = await doc.get_file()
                ext = os.path.splitext(doc.file_name or "")[1] or ".bin"
                filename = f"material_{correo}_perfil{i}{ext}"
                await file.download_to_drive(filename)
                logging.info(f"Material guardado: {filename}")
            elif has_photo:
                # tomar la foto de mayor tamaño
                photo = update.message.photo[-1]
                file = await photo.get_file()
                filename = f"material_{correo}_perfil{i}.jpg"
                await file.download_to_drive(filename)
                logging.info(f"Material guardado: {filename}")
        except Exception as e:
            logging.exception(f"Error descargando material (perfil {i}): {e}")
            await update.message.reply_text("❌ Error al descargar el material. Intenta nuevamente.")
            return AGREGAR_MATERIAL

    await update.message.reply_text(f"✅ Material guardado para {perfiles} perfil(es). Registro finalizado.")
    tmp_venta.pop(user_id, None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel - Cancela el flujo de conversación actual."""
    user_id = update.message.from_user.id
    
    if user_id in tmp_venta:
        tmp_venta.pop(user_id)
    if user_id in tmp_reporte:
        tmp_reporte.pop(user_id)
        
    # Limpiar estado de borrado si está activo
    context.user_data.pop('awaiting_delete_index', None)
    context.user_data.pop('filtered_stock', None)
    context.user_data.pop('stock_to_delete', None)
        
    await update.message.reply_text("❌ Proceso cancelado.")
    return ConversationHandler.END


# --- Comandos Generales y Stock ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start - Muestra la imagen de bienvenida con ID y luego el menú principal.
    Muestra comandos según el rol."""
    # Determinar usuario
    if getattr(update, "message", None):
        user = update.message.from_user
    elif getattr(update, "callback_query", None):
        user = update.callback_query.from_user
    else:
        user = update.effective_user
    if not user:
        return

    user_mention = f"@{user.username}" if getattr(user, "username", None) else (user.first_name or "Usuario")
    user_id = user.id
    inicializar_usuario(user_id)

    # Definir textos de comandos aquí para usarlos en la bienvenida
    admin_comandos = (
        "👑 *Comandos de Administrador:*\n"
        "/start - Iniciar el bot y ver el menú principal.\n"
        "/saldo - Ver tu saldo.\n"
        "/comandos - Muestra esta lista.\n"
        "/stock - Ver el inventario detallado de cuentas.\n"
        "/addventa <Plataforma> - Agregar cuenta al stock.\n"
        "/borrarventa - Eliminar cuenta del stock.\n"
        "/recargar <ID> <monto> - Recarga saldo a un usuario.\n"
        "/quitarsaldo <ID> <monto> - Descuenta saldo a un usuario.\n"
        "/consultarsaldo <ID> - Consulta saldo de un usuario.\n"
        "/historial - Obtén el CSV de tu historial de compras.\n"
        "/cancel - Cancela un flujo de conversación.\n"
        "/addcombo - Inicia flujo para agregar combos.\n"
        "/combos - Muestra combos disponibles.\n"
        "/verclientes - Muestra lista de clientes.\n"
        "/responder <ID> <mensaje> - Enviar mensaje a cliente.\n"
        "/eliminarcliente <ID> - Elimina un cliente del registro.\n"
       
    )
    cliente_comandos = (
        "👤 *Comandos de Cliente:*\n"
        "/start - Iniciar el bot y ver el menú principal.\n"
        "/saldo - Ver tu saldo actual.\n"
        "/comandos - Muestra esta lista.\n"
        "/historial - Descarga tu historial de compras.\n"
        "/cancel - Cancela un flujo de conversación.\n"
        "/combos - Muestra los combos disponibles.\n"
        "⚠️ Reportar problema desde el menú principal.\n"
    )

    if is_admin(user_id):
        comandos_text = f"{admin_comandos}\n{cliente_comandos}"
    else:
        comandos_text = cliente_comandos

    bienvenida_text = (
        f"¡Bienvenido {user_mention}!\n\n"
        f"Tu ID es: {user_id}\n\n"
        "Si ya cuentas con créditos, estos comandos son para ti:\n\n"
        f"{comandos_text}\n"
        f"Para comprar créditos, contacta al administrador {ADMIN_USERNAME} {f'({ADMIN_PHONE})' if ADMIN_PHONE else ''}"
    )

    # Resolver ruta absoluta de la imagen (misma carpeta del script)
    script_dir = Path(__file__).resolve().parent
    image_path = script_dir / WELCOME_IMAGE

    logging.info(f"Start: buscando imagen de bienvenida en: {image_path}")

    # Telegram limita la longitud de caption (≈1024 chars). Si el texto es más largo,
    # enviamos la imagen SIN caption y luego el texto en un mensaje separado.
    MAX_CAPTION = 1024

    try:
        if WELCOME_IMAGE and image_path.exists():
            file_size = image_path.stat().st_size
            logging.info(f"Start: imagen encontrada (tamaño {file_size} bytes). Intentando enviar...")

            # Si el texto cabe en caption, intentar enviarlo como caption.
            use_caption = len(bienvenida_text) <= MAX_CAPTION

            with open(image_path, "rb") as img:
                try:
                    if getattr(update, "message", None):
                        if use_caption:
                            await update.message.reply_photo(photo=img, caption=bienvenida_text, parse_mode="Markdown")
                        else:
                            # enviar foto sin caption y luego el texto
                            await update.message.reply_photo(photo=img)
                            await update.message.reply_text(bienvenida_text, parse_mode="Markdown")
                    else:
                        if use_caption:
                            await context.bot.send_photo(chat_id=user_id, photo=img, caption=bienvenida_text, parse_mode="Markdown")
                        else:
                            await context.bot.send_photo(chat_id=user_id, photo=img)
                            await context.bot.send_message(chat_id=user_id, text=bienvenida_text, parse_mode="Markdown")
                except Exception as e_photo:
                    logging.warning(f"Fallo send_photo: {e_photo}. Intentando enviar como documento sin caption largo...")
                    # En caso de fallo al enviar como photo, enviamos como documento.
                    with open(image_path, "rb") as doc:
                        try:
                            if getattr(update, "message", None):
                                # nunca usar caption largo; enviamos documento sin caption y texto aparte
                                await update.message.reply_document(document=doc)
                                await update.message.reply_text(bienvenida_text, parse_mode="Markdown")
                            else:
                                await context.bot.send_document(chat_id=user_id, document=doc)
                                await context.bot.send_message(chat_id=user_id, text=bienvenida_text, parse_mode="Markdown")
                        except Exception as e_doc:
                            logging.exception(f"Fallo al enviar documento de bienvenida: {e_doc}")
                            # último recurso: solo enviar texto
                            if getattr(update, "message", None):
                                await update.message.reply_text(bienvenida_text, parse_mode="Markdown")
                            else:
                                await context.bot.send_message(chat_id=user_id, text=bienvenida_text, parse_mode="Markdown")
        else:
            logging.warning(f"Start: imagen no encontrada en {image_path} (WELCOME_IMAGE='{WELCOME_IMAGE}')")
            if getattr(update, "message", None):
                await update.message.reply_text(bienvenida_text, parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=user_id, text=bienvenida_text, parse_mode="Markdown")
    except Exception as e:
        logging.exception(f"No se pudo enviar la imagen/texto de bienvenida: {e}")
        # Último recurso: enviar sólo texto
        await context.bot.send_message(chat_id=user_id, text=bienvenida_text, parse_mode="Markdown")

    await show_main_menu(update, context, welcome_msg="✅ ¿Qué deseas hacer ahora?")


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/saldo - Muestra el saldo actual del usuario."""
    user_id = update.message.from_user.id
    inicializar_usuario(user_id)
    await update.message.reply_text(f"💳 Tu saldo actual es: ${clientes[user_id]:.2f}", parse_mode="Markdown")

async def consultar_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/consultarsaldo <ID_USUARIO> (Admin) - Consulta el saldo de un cliente."""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("❌ Uso Admin: /consultarsaldo <ID_USUARIO>")
        return

    try:
        target_id = int(context.args[0])
        
        inicializar_usuario(target_id)
        target_saldo = clientes.get(target_id, 0.00)
        
        await update.message.reply_text(
            f"✅ Saldo del usuario ID {target_id}: ${target_saldo:.2f}", 
            parse_mode="Markdown"
        )
        
    except ValueError:
        await update.message.reply_text("❌ ID de usuario inválido. Debe ser un número entero.")

async def recargar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/recargar <ID_USUARIO> <monto> (Admin) o Instrucciones (Usuario)."""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        inicializar_usuario(user_id)
        back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
        await update.message.reply_text(
            f"💰 Tu saldo actual es: ${clientes.get(user_id, 0):.2f}\n\n"
            f"Para recargar, contacta al administrador e indica tu ID de usuario: {user_id}.",
            reply_markup=back_keyboard,
            parse_mode="Markdown"
        )
        return

    # Lógica de simulación de recarga para el ADMIN
    if not context.args or len(context.args) != 2:
        await update.message.reply_text("❌ Uso Admin: /recargar <ID_USUARIO> <monto>")
        return

    try:
        target_id = int(context.args[0])
        monto = float(context.args[1])
        
        if monto <= 0:
            await update.message.reply_text("❌ El monto debe ser positivo.")
            return

        inicializar_usuario(target_id)
        clientes[target_id] += monto
        guardar_clientes()
        
        await update.message.reply_text(f"✅ Recarga exitosa a ID {target_id} de ${monto:.2f}. Saldo actual: ${clientes[target_id]:.2f}")
        
        try:
            await context.bot.send_message(
                chat_id=target_id, 
                text=f"🎉 Tu saldo ha sido recargado con ${monto:.2f} por el administrador. Saldo actual: ${clientes[target_id]:.2f}",
                parse_mode="Markdown"
            )
        except Exception:
             logging.warning(f"No se pudo enviar notificación al usuario {target_id}.")
            
    except ValueError:
        await update.message.reply_text("❌ ID de usuario o Monto inválido. Ambos deben ser números (el monto puede ser decimal).")

async def quitar_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/quitarsaldo <ID_USUARIO> <monto> (Admin)"""
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return

    if not context.args or len(context.args) != 2:
        await update.message.reply_text("❌ Uso Admin: /quitarsaldo <ID_USUARIO> <monto>")
        return

    try:
        target_id = int(context.args[0])
        monto = float(context.args[1])
        
        if monto <= 0:
            await update.message.reply_text("❌ El monto debe ser positivo.")
            return

        inicializar_usuario(target_id)
        
        clientes[target_id] = max(0, clientes[target_id] - monto)
        guardar_clientes()
        
        await update.message.reply_text(f"✅ Se han descontado ${monto:.2f} a ID {target_id}. Saldo actual: ${clientes[target_id]:.2f}")
        
        try:
            await context.bot.send_message(
                chat_id=target_id, 
                text=f"⚠️ Se ha descontado ${monto:.2f} de tu saldo por el administrador. Saldo actual: ${clientes[target_id]:.2f}",
                parse_mode="Markdown"
            )
        except Exception:
             logging.warning(f"No se pudo enviar notificación al usuario {target_id}.")
            
    except ValueError:
        await update.message.reply_text("❌ ID de usuario o Monto inválido. Ambos deben ser números (el monto puede ser decimal).")

async def comandos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/comandos - Muestra los comandos disponibles según el rol del usuario."""
    user_id = update.message.from_user.id
    
    admin_comandos = (
        "👑 *Comandos de Administrador:*\n"
        "------------------------------------\n"
        "/start - Iniciar el bot y ver el menú principal.\n"
        "/saldo - Ver tu saldo.\n"
        "/comandos - Muestra esta lista.\n"
        "/stock - Ver el inventario detallado de cuentas.\n"
        "/addventa <Plataforma> - Iniciar el flujo para agregar una cuenta al stock.\n"
        "/borrarventa - Iniciar el flujo para eliminar una cuenta del stock.\n"
        "/recargar <ID> <monto> - Recarga saldo a un usuario.\n"
        "/quitarsaldo <ID> <monto> - Descuenta saldo a un usuario.\n"
        "/consultarsaldo <ID> - Consulta el saldo de un usuario específico.\n"
        "/historial - Obtén el CSV con el historial de tus compras.\n"
        "/cancel - Cancela un flujo de conversación (e.g., /addventa, /borrarventa o Reporte).\n"
        "/addcombo - Inicia el flujo para agregar un nuevo combo de cuentas.\n"
        "/combos - Muestra los combos disponibles para compra.\n"
        "/verclientes - Muestra la lista de clientes con su ID y saldo.\n"
        "/responder <ID> <mensaje> - Responde a reportes o envía mensajes a clientes.\n"
        "/eliminarcliente <ID> - Elimina un cliente del registro.\n"
  
    )
    cliente_comandos = (
        "👤 *Comandos de Cliente:*\n"
        "------------------------------------\n"
        "/start - Iniciar el bot y ver el menú principal.\n"
        "/saldo - Ver tu saldo actual.\n"
        "/comandos - Muestra esta lista.\n"
        "/historial - Obtén el CSV con el historial de tus compras.\n"
        "/cancel - Cancela un flujo de conversación.\n"
        "/combos - Muestra los combos disponibles para compra.\n"
        "⚠️ Reportar problema desde el menú principal.\n"
    )
    if is_admin(user_id):
        mensaje = f"{admin_comandos}\n\n{cliente_comandos}"
    else:
        mensaje = cliente_comandos
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def stock_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stock - Muestra el inventario disponible agrupado por tipo (solo Admin)."""
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede ver el inventario.")
        return

    cleaned_stock = cleanup_stock()
    stock_info = get_dynamic_stock_info()

    # Contar por plataforma y categoría
    counts = defaultdict(lambda: defaultdict(int))  # counts[platform][categoria] = cantidad
    for row in cleaned_stock:
        if len(row) < 2:
            continue
        platform = row[0].strip()
        tipo = row[1].strip()
        tipo_lower = tipo.lower()
        categoria = 'otro'
        if 'perfil' in tipo_lower and 'completa' not in tipo_lower:
            categoria = 'perfil'
        elif 'completa' in tipo_lower and 'perfil' not in tipo_lower:
            categoria = 'completa'
        else:
            if tipo_lower.startswith(('1 perfil', 'perfil')):
                categoria = 'perfil'
            elif tipo_lower.startswith(('cuenta', 'full', 'premium', 'basico', 'estandar', 'completa')):
                categoria = 'completa'
            else:
                categoria = 'otro'

        if categoria == 'perfil':
            # sumar perfiles_disponibles si existe
            if len(row) >= 7:
                try:
                    perfiles_disponibles = int(row[5])
                    counts[platform]['perfil'] += max(0, perfiles_disponibles)
                except (ValueError, TypeError):
                    counts[platform]['perfil'] += 0
            else:
                counts[platform]['perfil'] += 1
        elif categoria == 'completa':
            counts[platform]['completa'] += 1
        else:
            counts[platform]['otro'] += 1

    if not counts:
        await update.message.reply_text("📦 El inventario está vacío.")
        return

    message = "📦 Inventario Actual:\n"
    for platform in sorted(counts.keys()):
        message += f"\n*--- {platform.upper()} ---*\n"
        # Para cada categoría en orden
        for categoria in ('completa', 'perfil', 'otro'):
            cnt = counts[platform].get(categoria, 0)
            if cnt <= 0:
                continue
            # Obtener precio mínimo conocido desde stock_info
            precio = None
            if categoria in stock_info and platform in stock_info[categoria]:
                p = stock_info[categoria][platform]['precio']
                precio = p if p != float('inf') else None

            if precio is None:
                precio_f = 0.0
            else:
                precio_f = float(precio)

            display_tipo = categoria if categoria in ('completa', 'perfil') else ', '.join(sorted(stock_info.get('otro', {}).get(platform, {}).get('tipos_disponibles', []))) or 'otro'
            # Asegurar display simple para perfil (sin número)
            if categoria == 'perfil':
                display_tipo = 'perfil'
            elif categoria == 'completa':
                display_tipo = 'completa'

            message += f"▪️ {display_tipo} - ${precio_f:.2f} (Disponibles: {cnt})\n"

    await update.message.reply_text(message, parse_mode="Markdown")

def log_compra_global(user_id, plan, correo, password, precio, id_compra):
    """Registra la compra en el archivo de historial global."""
    file_exists = os.path.exists(COMPRAS_FILE)
    with open(COMPRAS_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists or os.path.getsize(COMPRAS_FILE) == 0:
            writer.writerow(['ID_Compra', 'ID_Usuario', 'Fecha', 'Plan', 'Correo', 'Contraseña', 'Precio'])
        
        fecha = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        writer.writerow([id_compra, user_id, fecha, plan, correo, password, f"{precio:.2f}"])
    
    logging.info(f"Compra global registrada: {id_compra} para usuario {user_id}")

def log_compra(user_id, plan, correo, password, precio, id_compra):
    """Registra la compra del usuario en su archivo de historial."""
    historial_file = f'historial_{user_id}.csv'
    fecha = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    file_exists = os.path.exists(historial_file)
    with open(historial_file, 'a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists or os.path.getsize(historial_file) == 0:
            writer.writerow(['ID_Compra', 'Fecha', 'Plan', 'Correo', 'Contraseña', 'Precio'])
        
        writer.writerow([id_compra, fecha, plan, correo, password, f"{precio:.2f}"])
    
    logging.info(f"Compra registrada in historial_{user_id}.csv: {plan}")
    
    # También se registra en el historial global
    log_compra_global(user_id, plan, correo, password, precio, id_compra)


async def historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/historial - Envía al usuario el archivo CSV con todas sus compras."""
    user_id = update.message.from_user.id
    historial_file = f'historial_{user_id}.csv'
    
    if os.path.exists(historial_file) and os.path.getsize(historial_file) > 0:
        # Usar with para cerrar el archivo correctamente
        with open(historial_file, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"historial_compras_{user_id}.csv",
                caption="📂 Aquí tienes el historial de todas tus compras."
            )
    else:
        await update.message.reply_text("❌ Aún no tienes compras registradas en tu historial.")

def entregar_cuenta(plataforma: str, tipo: str, precio_buscado: float):
    """Entrega el siguiente perfil/disponible y actualiza el stock.
    Soporta filas de:
      - completa: [plataforma, tipo, correo, pass, precio]
      - perfil:   [plataforma, tipo, correo, pass, precio, perfiles_disponibles, perfil_actual]
    Devuelve [plataforma, tipo, correo, password, precio, perfil_entregado]
    perfil_entregado == 0 -> cuenta completa; >0 -> número de perfil entregado.
    """
    cuentas = load_stock()
    for i, row in enumerate(cuentas):
        if len(row) < 5:
            continue

        stock_plataforma = row[0].strip()
        stock_tipo = row[1].strip()
        correo = row[2] if len(row) > 2 else ''
        password = row[3] if len(row) > 3 else ''
        # Precio protegido y normalizado
        try:
            stock_precio = float(str(row[4]).strip())
        except (ValueError, TypeError):
            continue

        # Coincidencia de plataforma/tipo/precio (tolerancia pequeña)
        if stock_plataforma.strip().lower() != plataforma.strip().lower():
            continue
        if stock_tipo.strip().lower() != tipo.strip().lower():
            continue
        if abs(stock_precio - precio_buscado) > 0.01:
            continue

        # Caso: cuenta por perfil (con campos 5 y 6)
        if len(row) >= 7:
            try:
                perfiles_disponibles = int(row[5])
                perfil_actual = int(row[6])
            except (ValueError, TypeError):
                # Datos corruptos -> saltar fila
                continue

            perfil_entregado = perfil_actual
            if perfiles_disponibles > 1:
                # CORRECCIÓN: usar 'perfiles_disponibles' (no 'perfil_disponibles')
                cuentas[i][5] = str(perfiles_disponibles - 1)
                cuentas[i][6] = str(perfil_actual + 1)
            else:
                # Último perfil: eliminar la fila
                del cuentas[i]
            save_stock(cuentas)
            # Hacer limpieza adicional por si hay filas con perfiles <=0
            cleanup_stock()
            return [stock_plataforma, stock_tipo, correo, password, stock_precio, perfil_entregado]

        # Caso: cuenta completa (no perfiles) -> eliminar la fila al entregar
        else:
            del cuentas[i]
            save_stock(cuentas)
            cleanup_stock()
            return [stock_plataforma, stock_tipo, correo, password, stock_precio, 0]

    return None


# --- Flujo de Borrado de Stock (Admin) ---

async def borrar_venta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/borrarventa - Muestra la lista de stock para eliminar (solo Admin)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.callback_query:
             await update.callback_query.edit_message_text("❌ Solo el administrador puede borrar ventas.")
        else:
             await update.message.reply_text("❌ Solo el administrador puede borrar ventas.")
        return

    stock_list = load_stock()
    if not stock_list:
        if update.callback_query:
            await update.callback_query.edit_message_text("📦 El inventario está vacío. No hay nada para borrar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]]))
        else:
            await update.message.reply_text("📦 El inventario está vacío. No hay nada para borrar.")
        return
        
    # Agrupar por tipo (Completa/Perfil) para facilitar la navegación
    stock_by_category = defaultdict(list)
    for row in stock_list:
        tipo_lower = row[1].lower()
        # FIX: Categorización para borrado
        if 'completa' in tipo_lower and 'perfil' not in tipo_lower:
            stock_by_category['completa'].append(row)
        elif 'perfil' in tipo_lower:
            stock_by_category['perfil'].append(row)
        else:
            stock_by_category['otro'].append(row)

    context.user_data['stock_to_delete'] = stock_list # Guardamos la lista completa para referencia

    keyboard = []
    if stock_by_category['completa']:
        keyboard.append([InlineKeyboardButton(f"🥇 Cuentas Completas ({len(stock_by_category['completa'])})", callback_data="borrar_completa")])
    if stock_by_category['perfil']:
        keyboard.append([InlineKeyboardButton(f"👥 Cuentas por Perfil ({len(stock_by_category['perfil'])})", callback_data="borrar_perfil")])
    if stock_by_category['otro']:
         keyboard.append([InlineKeyboardButton(f"❓ Otros Tipos ({len(stock_by_category['otro'])})", callback_data="borrar_otro")])

    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="empezar")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = "🗑️ Selecciona la categoría de stock que deseas eliminar: "

    if update.callback_query:
        await update.callback_query.edit_message_text(
            message, 
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            message, 
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def mostrar_lista_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la lista de stock filtrada por categoría."""
    query = update.callback_query
    await query.answer()

    data = query.data.replace('borrar_', '') # 'completa', 'perfil', 'otro'
    stock_list = context.user_data.get('stock_to_delete')
    
    if not stock_list:
        await query.edit_message_text("❌ Error: Stock no encontrado en la sesión. Usa /borrarventa de nuevo.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]]))
        return
        
    filtered_stock = []
    for row in stock_list:
        if len(row) < 2:
            continue
        tipo = row[1].lower()
        if (data == 'completa' and 'completa' in tipo and 'perfil' not in tipo):
            filtered_stock.append(row)
        elif (data == 'perfil' and 'perfil' in tipo):
            filtered_stock.append(row)
        elif (data == 'otro' and 'completa' not in tipo and 'perfil' not in tipo):
            filtered_stock.append(row)

    if not filtered_stock:
        await query.edit_message_text(f"❌ No hay stock de tipo '{data}' para eliminar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver", callback_data="borrar_venta_menu")]]))
        return

    context.user_data['filtered_stock'] = filtered_stock
    
    message = f"🗑️ Stock Disponible para Borrar ({data.capitalize()}):\n\n"
    
    for i, row in enumerate(filtered_stock):
        platform = row[0] if len(row) > 0 else 'N/A'
        tipo = row[1] if len(row) > 1 else 'N/A'
        correo = row[2] if len(row) > 2 else 'N/A'
        precio = row[4] if len(row) > 4 else (row[-2] if len(row) >= 2 else '0')
        try:
            precio_f = float(precio)
        except (ValueError, TypeError):
            precio_f = 0.0
        message += f"{i+1}. {platform} ({tipo}) - ${precio_f:.2f} - Correo: {correo}\n"
        
    message += f"\n👉 Envía el número (1 a {len(filtered_stock)}) de la cuenta que deseas eliminar, o /cancel."
    
    keyboard = [[InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="borrar_venta_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        message, 
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    context.user_data['awaiting_delete_index'] = True

async def borrar_stock_por_indice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la eliminación del stock seleccionada por índice (Message Handler)."""
    user_id = update.message.from_user.id
    
    # 1. Verificar si estamos esperando un índice
    if not context.user_data.get('awaiting_delete_index') or not is_admin(user_id):
        return 

    try:
        index_to_delete = int(update.message.text.strip()) - 1
    except ValueError:
        await update.message.reply_text("❌ Por favor, ingresa un número válido.")
        return 

    filtered_stock = context.user_data.get('filtered_stock')

    if not filtered_stock:
        await update.message.reply_text("❌ Error en la sesión de borrado. Usa /borrarventa para empezar de nuevo.")
        context.user_data.pop('awaiting_delete_index', None)
        return

    if index_to_delete < 0 or index_to_delete >= len(filtered_stock):
        await update.message.reply_text(f"❌ Número fuera de rango. Debe ser entre 1 y {len(filtered_stock)}.")
        return 

    # 2. Obtener la cuenta a eliminar y buscar en la lista completa
    item_to_delete = filtered_stock[index_to_delete]
    all_stock = load_stock()
    found = False
    
    # Buscamos y eliminamos la primera ocurrencia que coincida exactamente (todos los campos)
    for i, row in enumerate(all_stock):
        if row == item_to_delete: 
            del all_stock[i]
            found = True
            break
            
    # 3. Guardar la cuenta eliminada en el archivo de stock
    if found:
        platform, tipo, correo, _, precio = item_to_delete
        save_stock(all_stock)
        
        await update.message.reply_text(
            f"✅ Cuenta eliminada:\n*{platform}* ({tipo}) - Correo: {correo}\n\nUsa /borrarventa para seguir eliminando o /start para ir al menú principal.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ Error: No se pudo encontrar la cuenta exacta en el archivo principal. El stock puede estar desincronizado. Intenta /borrarventa de nuevo.",
            parse_mode="Markdown"
        )

    # 4. Limpiar el estado de la conversación
    context.user_data.pop('awaiting_delete_index', None)
    context.user_data.pop('filtered_stock', None)
    context.user_data.pop('stock_to_delete', None)
    

# --- Flujo de Compra: Selección de Categoría, Plataforma y Tipo ---

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra la selección de categorías (Perfiles/Completas).
    """
    query = update.callback_query
    # Intentar responder; si falla (expired), continuar y usar fallback
    try:
        await query.answer()
    except BadRequest as e:
        logging.debug(f"show_categories: query.answer falló (posible expirado): {e}")
    except Exception as e:
        logging.debug(f"show_categories: query.answer unexpected: {e}")

    stock_info = get_dynamic_stock_info()
    
    has_completa = 'completa' in stock_info and stock_info['completa']
    has_perfil = 'perfil' in stock_info and stock_info['perfil']

    if not has_completa and not has_perfil:
        try:
            await query.edit_message_text(
                "❌ No hay stock disponible en este momento. Vuelve más tarde.", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
            )
        except Exception as e:
            logging.warning(f"show_categories: fallo edit_message_text (sin stock): {e}")
            try:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text="❌ No hay stock disponible en este momento. Vuelve más tarde.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
                )
            except Exception as e2:
                logging.exception(f"show_categories: fallo fallback send_message (sin stock): {e2}")
        return

    keyboard = []
    if has_completa:
        keyboard.append([InlineKeyboardButton("🥇 Cuentas Completas", callback_data="category_completa")])
    if has_perfil:
        keyboard.append([InlineKeyboardButton("👥 Cuentas por Perfil", callback_data="category_perfil")])

    keyboard.append([InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    texto = "✅ Cuentas disponibles:\n\nSelecciona si quieres Perfiles o Completas:"

    # Intentar editar; si falla, enviar nuevo mensaje como fallback
    try:
        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.warning(f"show_categories: fallo edit_message_text, enviando nuevo mensaje: {e}")
        try:
            await context.bot.send_message(chat_id=query.from_user.id, text=texto, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e2:
            logging.exception(f"show_categories: fallo send_message fallback: {e2}")


async def show_plataformas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra las plataformas disponibles DENTRO de la categoría seleccionada.
    """
    query = update.callback_query
    # Intentar responder; si falla (expired), continuar y usar fallback
    try:
        await query.answer()
    except BadRequest as e:
        logging.debug(f"show_categories: query.answer falló (posible expirado): {e}")
    except Exception as e:
        logging.debug(f"show_categories: query.answer unexpected: {e}")

    category = query.data.replace('category_', '')
    stock_info = get_dynamic_stock_info()
    platforms_in_category = stock_info.get(category, {})
    if not platforms_in_category:
        # usar helper seguro abajo para edición/fallback
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="show_categories")]])
        texto = f"❌ No hay stock de {category.capitalize()} disponible en este momento."
        try:
            await query.edit_message_text(texto, reply_markup=back_markup)
        except BadRequest:
            logging.debug(f"show_categories: edit_message_text expirado, enviando nuevo mensaje: {e}")
            await context.bot.send_message(chat_id=query.from_user.id, text=texto, reply_markup=back_markup)
        except Exception as e:
            logging.exception(f"show_categories: fallo inesperado edit_message_text: {e}")
        return

    keyboard = []
    for platform, data in sorted(platforms_in_category.items()):
        precio_min = data['precio']
        clean_platform = platform.replace(' ', '~')
        keyboard.append([InlineKeyboardButton(f"▶️ {platform} (Desde ${precio_min:.2f})", callback_data=f"select_{category}_{clean_platform}")])
    keyboard.append([InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="show_categories")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    texto = f"✅ {category.capitalize()} Disponibles:\n\nSelecciona una plataforma:"

    try:
        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as e:
        logging.debug(f"show_plataformas: edit_message_text expirado, enviando nuevo mensaje: {e}")
        await context.bot.send_message(chat_id=query.from_user.id, text=texto, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.exception(f"show_plataformas: fallo inesperado edit_message_text: {e}")


async def handle_platform_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Todas las compras son directas: al seleccionar una plataforma, se compra el primer stock disponible.
    No se muestran submenús de tipos/perfiles.
    """
    query = update.callback_query
    await query.answer()

    parts = query.data.split('_')
    category = parts[1]
    platform_parts = parts[2:]
    platform = "_".join(platform_parts).replace('~', ' ')

    # Buscar el primer stock disponible de la plataforma y categoría seleccionada
    all_stock = load_stock()
    for row in all_stock:
        # Protección contra filas cortas o malformadas
        if not row or len(row) < 2:
            logging.debug(f"handle_platform_selection: fila inválida ignorada: {row}")
            continue

        stock_platform = row[0].strip() if len(row) > 0 else ''
        stock_tipo = row[1].strip() if len(row) > 1 else ''
        precio_str = row[4] if len(row) > 4 else row[-1]
        try:
            stock_precio = float(str(precio_str).strip())
        except (ValueError, TypeError):
            continue

        tipo_lower = stock_tipo.lower()
        plataforma_clave = stock_platform.strip()
        
        categoria = 'otro'
        if 'perfil' in tipo_lower and 'completa' not in tipo_lower:
            categoria = 'perfil'
        elif 'completa' in tipo_lower and 'perfil' not in tipo_lower:
            categoria = 'completa'
        else:
            # Lógica de respaldo
            if tipo_lower.startswith(('1 perfil', 'perfil')):
                categoria = 'perfil'
            elif tipo_lower.startswith(('cuenta', 'full', 'premium', 'basico', 'estandar', 'completa')):
                categoria = 'completa'

        if stock_platform.strip().lower() == platform.strip().lower() and categoria == category:
            try:
                clean_platform = platform.replace('~', ' ')
                clean_type = stock_tipo.replace('~', ' ')
                callback_data = f"buy_{category}_{clean_platform}_{clean_type}_{stock_precio}"

                # Construir fake_update compatible: incluir callback_query, effective_user y message
                fake_query = SimpleNamespace()
                fake_query.data = callback_data
                fake_query.from_user = query.from_user
                fake_query.answer = query.answer
                fake_query.message = query.message

                fake_update = SimpleNamespace()
                fake_update.callback_query = fake_query
                fake_update.effective_user = query.from_user  # necesario para show_main_menu y otros
                fake_update.message = query.message  # por si alguna función usa update.message

                await handle_compra_final(fake_update, context, callback_data=callback_data)
                return
            except (ValueError, TypeError) as e:
                logging.debug(f"handle_platform_selection: precio inválido en fila {row}: {e}")
                continue

    # Si no hay stock
    await query.edit_message_text(
        f"❌ No hay stock disponible para {platform} en este momento.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="show_categories")]])
    )


async def handle_compra_final(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data=None):
    query = update.callback_query
    # RESPONDER AL CALLBACK PARA QUE EL CLIENTE DEJE DE CARGAR
    try:
        await query.answer()
    except Exception as e:
        logging.debug(f"Ignored query.answer() error in handle_compra_final: {e}")

    if callback_data is None:
        callback_data = query.data

    # 1. Validar saldo
    user_id = query.from_user.id
    inicializar_usuario(user_id)
    
    parts = callback_data.split('_')
    
    # Protección: validar estructura mínima del callback_data
    if len(parts) < 4:
        logging.error(f"Callback data malformado: {callback_data}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Error interno: datos de compra inválidos. Intenta de nuevo.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
        )
        return

    category = parts[1]
    price_str = parts[-1] 
    
    try:
        precio_final = float(price_str)
    except ValueError:
        logging.error(f"Precio inválido en callback_data: {callback_data}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Error en el precio. Compra cancelada.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
        )
        return

    prev_balance = clientes.get(user_id, 0.0)
    if prev_balance < precio_final:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Saldo insuficiente. Necesitas ${precio_final:.2f} y solo tienes ${prev_balance:.2f}.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 Recargar saldo", callback_data="mostrar_recarga")], [InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]]),
            parse_mode="Markdown"
        )
        return

    # 2. Entregar Cuenta y Descontar Stock
    # Extraer campos esperados con protección de índices
    clean_platform = parts[-3] if len(parts) >= 3 else ''
    clean_type = parts[-2] if len(parts) >= 2 else ''

    # Normalizar para uso en mensajes y para entregar_cuenta
    platform = clean_platform.replace('~', ' ')
    stock_type = clean_type.replace('~', ' ')

    logging.info(f"Compra solicitada por user {user_id}: categoria={category}, platform={platform}, type={stock_type}, precio={precio_final:.2f}")

    cuenta_data = entregar_cuenta(platform, stock_type, precio_final)

    if not cuenta_data:
        # Usar variables garantizadas para evitar NameError
        safe_platform = platform or clean_platform.replace('~', ' ')
        safe_stock_type = stock_type or clean_type.replace('~', ' ')
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Lo sentimos, el stock de {safe_platform} ({safe_stock_type}) se agotó justo antes de completar tu compra.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="show_categories")]])
        )
        return 

    # 3. Finalizar Transacción
    _, plan_entregado, correo, password, _, perfil_entregado = cuenta_data

    # Descontar saldo 
    clientes[user_id] -= precio_final 
    guardar_clientes()
    remaining = clientes[user_id]

    # Generar ID de Compra
    id_compra = str(uuid.uuid4()).split('-')[0].upper() # Genera un ID corto y aleatorio

    # Log de la compra
    log_compra(user_id, plan_entregado, correo, password, precio_final, id_compra)

    # 4. Enviar cuenta al usuario (NUEVO MENSAJE)
    logging.info(f"Entrega preparada: cuenta_data={cuenta_data}, user_id={user_id}, precio={precio_final:.2f}, saldo_restante={remaining:.2f}, id_compra={id_compra}")

    # Perfil / dispositivos
    # Para cuentas completas queremos mostrar que se entregaron "Todos los perfiles"
    # y que el cliente obtiene "1 dispositivo por perfil". Para cuentas por perfil
    # mantenemos el comportamiento anterior.
    if perfil_entregado == 0:
        perfil_text = "Todos los perfiles"
        dispositivos_text = "1 dispositivo por perfil"
    else:
        perfil_text = f"Perfil {perfil_entregado}"
        dispositivos_text = "1 dispositivo"

    mensaje_entrega = (
        "🎉 ¡Tu cuenta ha sido entregada! 🎉\n"
        "--------------------------------------\n"
        f"➡️ Plataforma: {platform}\n"
        f"➡️ Correo: {correo}\n"
        f"➡️ Contraseña: {password}\n"
        f"➡️ Perfiles asignados: {perfil_text}\n"
        f"➡️ Dispositivos: {dispositivos_text}\n"
        f"➡️ Costo: ${precio_final:.2f}\n"
        "--------------------------------------\n"
        f"🛡️ Garantía: {GARANTIA_DIAS} días\n"
        f"🆔 ID de Compra: {id_compra}\n\n"
        f"🔻 Se descontó: ${precio_final:.2f}\n"
        f"💳 Saldo restante: ${remaining:.2f}\n\n"
        "Guarda este ID para cualquier reporte. ¡Disfruta!\n"
    )

    # Intentar enviar el mensaje principal con manejo de errores
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=mensaje_entrega,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.exception(f"Error enviando mensaje de entrega al usuario {user_id}: {e}")
        # Notificar al admin por si falla el envío al cliente
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ Error enviando entrega a {user_id}: {e}")
        except Exception:
            logging.exception("No se pudo notificar al admin sobre el fallo de entrega.")

    # Envío de material adjunto (si existe) — usar with y captura de errores
    material_filename = f"material_{correo}_perfil{perfil_entregado}.jpg"  # o .pdf, .png, etc.
    if os.path.exists(material_filename):
        try:
            with open(material_filename, 'rb') as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    caption=f"Material para tu {perfil_text}"
                )
        except Exception as e:
            logging.exception(f"No se pudo enviar material '{material_filename}' a {user_id}: {e}")
            # continuar con el flujo, no abortar

    # 5. Abrir automáticamente el menú principal (NUEVO MENSAJE)
    await show_main_menu(update, context, welcome_msg="✅ Compra exitosa. ¿Qué deseas hacer ahora?")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, welcome_msg="Elige una opción:"):
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    inicializar_usuario(user_id)
    
    keyboard = [
        [InlineKeyboardButton("🛒 Comprar cuentas", callback_data="show_categories")], 
        [InlineKeyboardButton("🎁 Combos disponibles", callback_data="show_combos_menu")],  # Nuevo botón
        [InlineKeyboardButton("💰 Recargar saldo", callback_data="mostrar_recarga")],
        [InlineKeyboardButton("⚠️ Reportar problema", callback_data="iniciar_reporte")],
        [InlineKeyboardButton(f"💳 Saldo current: ${clientes[user_id]:.2f}", callback_data="saldo_info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Si viene de un callback_query, editar; si no, enviar nuevo mensaje
    if getattr(update, "callback_query", None):
        try:
            await update.callback_query.edit_message_text(welcome_msg, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
             await context.bot.send_message(
                 chat_id=user_id,
                 text=welcome_msg,
                 reply_markup=reply_markup,
                 parse_mode="Markdown"
             )
    else:
        await context.bot.send_message(
             chat_id=user_id,
             text=welcome_msg,
             reply_markup=reply_markup,
             parse_mode="Markdown"
         )
        
# Helper: construir texto y teclado de recarga
def _build_recarga_info(user_id: int):
    whatsapp = ADMIN_WHATSAPP or "(no configurado)"
    bank = BANK_ACCOUNT or "(no configurada)"
    min_text = f"${MIN_RECARGA:.2f}"
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
    texto = (
        f"💰 Tu saldo actual es: ${clientes.get(user_id, 0):.2f}\n\n"
        "Para recargar realiza una transferencia o depósito y envía el comprobante por WhatsApp con tu ID de cliente.\n\n"
        f"📲 WhatsApp (envía comprobante): {whatsapp}\n"
        f"🏦 Cuenta / Referencia: {bank}\n\n"
        f"🔎 Tu ID de cliente (indícalo en el comprobante/WhatsApp): `{user_id}`\n"
        f"⚠️ Mínimo de recarga: {min_text} pesos.\n\n"
        "Envía el comprobante al WhatsApp indicado junto con tu ID de cliente para que procesemos la recarga."
    )
    return texto, back_keyboard

async def recargar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/recargar <ID_USUARIO> <monto> (Admin) o muestra instrucciones (Usuario)."""
    user = update.effective_user or (update.message.from_user if update.message else None)
    if not user:
        return
    user_id = user.id

    # Admin: permitir recarga por comando
    if is_admin(user_id) and context.args and len(context.args) == 2:
        try:
            target_id = int(context.args[0])
            monto = float(context.args[1])
            if monto <= 0:
                await update.message.reply_text("❌ El monto debe ser positivo.")
                return
            inicializar_usuario(target_id)
            clientes[target_id] += monto
            guardar_clientes()
            await update.message.reply_text(f"✅ Recarga exitosa a ID {target_id} de ${monto:.2f}. Saldo actual: ${clientes[target_id]:.2f}")
            try:
                await context.bot.send_message(chat_id=target_id, text=f"🎉 Tu saldo ha sido recargado con ${monto:.2f} por el administrador. Saldo actual: ${clientes[target_id]:.2f}", parse_mode="Markdown")
            except Exception:
                logging.warning(f"No se pudo enviar notificación al usuario {target_id}.")
        except ValueError:
            await update.message.reply_text("❌ ID de usuario o Monto inválido. Ambos deben ser números.")
        return

    # Usuario normal -> mostrar instrucciones reutilizando el helper
    inicializar_usuario(user_id)
    texto, back_keyboard = _build_recarga_info(user_id)
    # si viene por callback no usamos update.message
    if getattr(update, "callback_query", None):
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        try:
            await update.callback_query.edit_message_text(texto, reply_markup=back_keyboard, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(chat_id=user_id, text=texto, reply_markup=back_keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(texto, reply_markup=back_keyboard, parse_mode="Markdown")

async def show_recarga_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para botón 'Recargar saldo' del menú — reutiliza el helper."""
    query = getattr(update, "callback_query", None)
    if query:
        try:
            await query.answer()
        except Exception:
            pass
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id if update.effective_user else (update.message.from_user.id if update.message else None)
        if not user_id:
            return

    texto, back_keyboard = _build_recarga_info(user_id)
    try:
        if query:
            await query.edit_message_text(texto, reply_markup=back_keyboard, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=texto, reply_markup=back_keyboard, parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(chat_id=user_id, text=texto, reply_markup=back_keyboard, parse_mode="Markdown")

# Helper: normalizar y validar fecha DD/MM/YYYY
def _normalize_fecha_input(text: str):
    """Intenta convertir entradas como '01012025', '01-01-2025', '1/1/25' a 'DD/MM/YYYY'.
    Devuelve la cadena formateada 'DD/MM/YYYY' si es válida, o None si no."""
    if not text:
        return None
    s = re.sub(r'[^0-9]', '', text)  # quitar todo lo que no sea dígito
    # aceptar ddmmyyyy (8), ddmmyy (6) => asumir siglo 2000
    if len(s) == 8:
        dd, mm, yyyy = s[:2], s[2:4], s[4:]
    elif len(s) == 6:
        dd, mm, yy = s[:2], s[2:4], s[4:]
        yyyy = '20' + yy
    else:
        return None
    try:
        dt = datetime.strptime(f"{dd}/{mm}/{yyyy}", "%d/%m/%Y")
        # devolver formato con ceros y 4 dígitos año
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return None

    # Robustizar validación de ID de compra: sanitiza y busca tanto en COMPRAS_FILE como en historial_{user}.csv
import string
def _sanitize_id(id_str: str) -> str:
    """Quita espacios, backticks y caracteres no alfanuméricos; devuelve en MAYÚSCULAS."""
    if not id_str:
        return ""
    s = str(id_str).strip()
    # quitar backticks y comillas y caracteres de formato comunes que el usuario puede copiar
    s = s.replace("`", "").replace("'", "").replace('"', "").replace("“", "").replace("”", "").strip()
    # conservar solo letras y números y guiones bajos/medios por si los IDs los incluyen
    allowed = set(string.ascii_letters + string.digits + "-_")
    cleaned = "".join(ch for ch in s if ch in allowed)
    return cleaned.upper()

def validar_id_compra(user_id: int, id_compra: str) -> bool:
    """Verifica si el ID de compra pertenece a user_id.
    Intenta leer los CSV probando varias codificaciones para evitar UnicodeDecodeError.
    Busca en compras_global.csv (CWD y carpeta del script) y en historial_{user_id}.csv."""
    id_clean = _sanitize_id(id_compra)
    if not id_clean:
        logging.info("validar_id_compra: id vacío después de sanitizar.")
        return False

    script_dir = Path(__file__).resolve().parent

    posibles_global = [
        Path(COMPRAS_FILE),              # ruta relativa al CWD
        script_dir / COMPRAS_FILE        # ruta en la carpeta del script
    ]

    posibles_hist = [
        Path(f"historial_{user_id}.csv"),
        script_dir / f"historial_{user_id}.csv"
    ]

    encodings_to_try = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]

    def _read_csv_try(path: Path):
        """Generator: intenta abrir path usando varias codificaciones y devuelve filas."""
        for enc in encodings_to_try:
            try:
                with path.open('r', newline='', encoding=enc) as f:
                    logging.debug(f"validar_id_compra: leyendo {path} con encoding={enc}")
                    reader = csv.reader(f)
                    for i, row in enumerate(reader, start=1):
                        yield enc, i, row
                return
            except UnicodeDecodeError as ude:
                logging.warning(f"validar_id_compra: fallo decoding {path} with {enc}: {ude}")
                continue
            except Exception as e:
                logging.exception(f"validar_id_compra: error abriendo {path} con {enc}: {e}")
                return
        # último intento: abrir con replacement para evitar excepciones
        try:
            with path.open('r', newline='', encoding=encodings_to_try[-1], errors='replace') as f:
                logging.warning(f"validar_id_compra: usando fallback errors='replace' para {path}")
                reader = csv.reader(f)
                for i, row in enumerate(reader, start=1):
                    yield encodings_to_try[-1], i, row
        except Exception as e:
            logging.exception(f"validar_id_compra: fallback fallo abriendo {path}: {e}")
            return

    logging.info(f"validar_id_compra: buscando ID {id_clean} para user {user_id} en rutas: {posibles_global + posibles_hist}")

    # Buscar en archivos globales
    for p in posibles_global:
        try:
            if not p.exists():
                logging.debug(f"validar_id_compra: {p} no existe, saltando.")
                continue
            for enc, i, row in _read_csv_try(p):
                if not row:
                    continue
                # Saltar cabecera si detectada
                first = (row[0] or "").strip().upper()
                if i == 1 and first.startswith("ID"):
                    continue
                row_id_raw = row[0] if len(row) > 0 else ""
                row_id = _sanitize_id(row_id_raw)
                row_user = None
                if len(row) > 1:
                    try:
                        row_user = int(str(row[1]).strip())
                    except Exception:
                        row_user = None
                logging.debug(f"validar_id_compra: {p} (enc={enc}) linea {i}: id_raw={row_id_raw!r} -> {row_id!r}, user_col={row_user}")
                if row_id == id_clean and row_user == user_id:
                    logging.info(f"validar_id_compra: encontrado en {p} (enc={enc}) -> {row}")
                    return True
        except Exception as e:
            logging.exception(f"validar_id_compra: error leyendo {p}: {e}")

    # Respaldo: buscar en historial_{user_id}.csv
    for p in posibles_hist:
        try:
            if not p.exists():
                logging.debug(f"validar_id_compra: historial {p} no existe, saltando.")
                continue
            for enc, i, row in _read_csv_try(p):
                if not row:
                    continue
                # No asumir formato fijo de cabecera; comprobación igual
                row_id_raw = row[0] if len(row) > 0 else ""
                row_id = _sanitize_id(row_id_raw)
                logging.debug(f"validar_id_compra (hist {p}) (enc={enc}): linea {i}: id_raw={row_id_raw!r} -> {row_id!r}")
                if row_id == id_clean:
                    logging.info(f"validar_id_compra: encontrado en historial {p} (enc={enc}) -> {row}")
                    return True
        except Exception as e:
            logging.exception(f"validar_id_compra: error leyendo historial {p}: {e}")

    logging.info(f"validar_id_compra: ID {id_compra} ({id_clean}) no encontrado para user {user_id}")
    return False

async def reporte_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Punto de entrada para el flujo de reporte.
    Responde al callback del botón, edita el mensaje si es posible o envía uno nuevo como fallback.
    Devuelve REPORTE_ID_COMPRA para iniciar la conversación."""
    query = getattr(update, "callback_query", None)

    # Determinar usuario de forma robusta
    user = None
    if query:
        try:
            await query.answer()
        except Exception:
            pass
        user = query.from_user
    else:
        user = update.effective_user or (update.message.from_user if getattr(update, "message", None) else None)

    if not user:
        logging.debug("reporte_start: no se pudo determinar el usuario.")
        return ConversationHandler.END

    user_id = user.id
    tmp_reporte[user_id] = {}

    texto = (
        "📝 Inicio del Reporte\n\n"
        "Ingresa el ID de Compra de la cuenta que presenta problemas (lo encuentras en el mensaje de entrega):"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])

    # Intentar editar el mensaje del teclado inline; si falla, enviar mensaje privado
    try:
        if query:
            await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user_id, text=texto, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as e:
        logging.debug(f"reporte_start: edit_message_text falló ({e}); enviando mensaje directo a {user_id}")
        try:
            await context.bot.send_message(chat_id=user_id, text=texto, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as ex:
            logging.exception(f"reporte_start: no se pudo enviar mensaje a {user_id}: {ex}")
            return ConversationHandler.END
    except Exception as e:
        logging.exception(f"reporte_start: error inesperado al iniciar reporte para {user_id}: {e}")
        return ConversationHandler.END

    return REPORTE_ID_COMPRA

async def reporte_id_compra_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1: Recibe el ID de Compra y lo valida. Sanea entrada y loggea para depuración."""
    user_id = update.message.from_user.id
    raw = (update.message.text or "").strip()
    id_compra = _sanitize_id(raw)  # usa la función de sanitización que ya definiste

    logging.info(f"reporte_id_compra_recibida: user={user_id} raw_input={raw!r} sanitized={id_compra!r}")

    if not id_compra or len(id_compra) < 4:
        await update.message.reply_text("❌ El ID de Compra es muy corto o está vacío. Debe tener al menos 4 caracteres alfanuméricos. Intenta nuevamente:")
        return REPORTE_ID_COMPRA

    # Detectar entradas claramente inválidas (ej: palabras como 'PERFIL', 'CUENTA', etc.)
    if any(word in id_compra for word in ("PERFIL", "CUENTA", "COMPLETA", "PERF")):
        await update.message.reply_text(
            "❌ Parece que has pegado algo que no es un ID (por ejemplo 'Perfil' o 'Cuenta'). "
            "Pega sólo el ID de compra que aparece en tu mensaje de entrega (ej: `A1B2C3D4`). Intenta de nuevo:"
        )
        return REPORTE_ID_COMPRA

    # VALIDACIÓN CLAVE
    try:
        if not validar_id_compra(user_id, id_compra):
            await update.message.reply_text(
                f"❌ ID de Compra inválido: El ID `{raw}` no se encuentra en tu historial de compras o no te pertenece. "
                "Verifica que el ID sea correcto e inténtalo de nuevo, o /cancel.",
                parse_mode="Markdown"
            )
            logging.debug(f"reporte_id_compra_recibida: validación fallida para user={user_id} id={id_compra}")
            return REPORTE_ID_COMPRA
    except Exception as e:
        logging.exception(f"reporte_id_compra_recibida: error validando id {id_compra} para user {user_id}: {e}")
        await update.message.reply_text("❌ Error interno validando el ID. Intenta más tarde o contacta al administrador.")
        return ConversationHandler.END

    tmp_reporte[user_id]['id_compra'] = id_compra
    await update.message.reply_text("✅ ID Validado. Ahora ingresa el correo de la cuenta:", parse_mode="Markdown")
    return REPORTE_CORREO

async def reporte_correo_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 2: Recibe el correo del reporte. Valida formato básico de email."""
    user_id = update.message.from_user.id
    correo = update.message.text.strip()
    if not correo:
        await update.message.reply_text("❌ El correo no puede estar vacío. Intenta nuevamente:")
        return REPORTE_CORREO

    # Validación simple de correo (básica, no exhaustiva)
    if not re.match(r"[^@]+@[^@]+\.[^@]+", correo):
        await update.message.reply_text("❌ Formato de correo inválido. Ejemplo válido: usuario@dominio.com. Intenta nuevamente:")
        return REPORTE_CORREO

    tmp_reporte[user_id]['correo'] = correo
    await update.message.reply_text("Ingresa la contraseña de la cuenta:", parse_mode="Markdown")
    return REPORTE_PASS

async def reporte_pass_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 3: Recibe y valida la contraseña."""
    user_id = update.message.from_user.id
    password = update.message.text.strip()
    if not password:
        await update.message.reply_text("❌ La contraseña no puede estar vacía. Intenta nuevamente:")
        return REPORTE_PASS

    tmp_reporte[user_id]['pass'] = password

    # Instrucción clara y ejemplo práctico; indicar que puede escribir sin '/' y se formatea
    await update.message.reply_text(
        "Ingresa la fecha en que compraste esta cuenta (Formato: DD/MM/AAAA).\n"
        "Puedes escribir por ejemplo: 01/01/2025 o 01012025 — la fecha se normalizará automáticamente.",
        parse_mode="Markdown"
    )
    return REPORTE_FECHA

async def reporte_fecha_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 4: Recibe la fecha y pide la descripción del problema."""
    user_id = update.message.from_user.id
    fecha_compra_str = update.message.text.strip()
    
    # Normalizar y validar fecha
    fecha_compra_norm = _normalize_fecha_input(fecha_compra_str)
    if not fecha_compra_norm:
        await update.message.reply_text(
            "❌ Formato de fecha incorrecto. Usa DD/MM/AAAA (ej: 01/10/2025). Intenta nuevamente:",
            parse_mode="Markdown"
        )
        return REPORTE_FECHA

    # Guardar fecha normalizada
    tmp_reporte[user_id]['fecha_compra'] = fecha_compra_norm
    
    await update.message.reply_text(
        "📝 Describe detalladamente el problema que presenta la cuenta. Si tienes una captura de pantalla, ¡puedes enviarla ahora mismo junto con tu texto!",
        parse_mode="Markdown"
    )
    return REPORTE_DESCRIPCION

async def reporte_descripcion_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 5: Recibe la descripción y/o foto y envía el reporte al Admin.
    Valida que todos los campos previos estén presentes antes de enviar."""
    user_id = update.message.from_user.id
    # Comprobar que aún existe el tmp_reporte para el usuario
    data = tmp_reporte.get(user_id)
    if not data:
        await update.message.reply_text("❌ No se encontró un reporte en curso. Inicia el reporte desde el menú y sigue los pasos.", parse_mode="Markdown")
        return ConversationHandler.END

    # Validar campos obligatorios recopilados
    missing = []
    for key, label in (('id_compra', 'ID de Compra'), ('correo', 'Correo'), ('pass', 'Contraseña'), ('fecha_compra', 'Fecha de compra')):
        if not data.get(key):
            missing.append(label)

    if missing:
        await update.message.reply_text(
            f"❌ Faltan campos obligatorios en el reporte: {', '.join(missing)}.\n"
            "Por favor reinicia el reporte con el botón 'Reportar problema' y completa todos los pasos.",
            parse_mode="Markdown"
        )
        # limpiar estado si lo deseas
        tmp_reporte.pop(user_id, None)
        return ConversationHandler.END

    descripcion = ""
    foto_id = None

    # Si el usuario envía texto
    if update.message.text:
        descripcion = update.message.text.strip()
    # Si el usuario envía foto
    if update.message.photo:
        foto_id = update.message.photo[-1].file_id
        if update.message.caption:
            descripcion = update.message.caption.strip()

    # Si no hay descripción textual, exigir al menos una descripción
    if not descripcion and not foto_id:
        await update.message.reply_text("❌ Debes proporcionar una descripción del problema o una foto. Intenta nuevamente:")
        return REPORTE_DESCRIPCION

    # Extraer y limpiar datos
    data = tmp_reporte.pop(user_id, {})
    reporte_msg = (
        "🚨 NUEVO REPORTE DE CUENTA\n"
        "-------------------------------\n"
        f"👤 Usuario ID: {user_id}\n"
        f"📧 Correo reportado: {data.get('correo','')}\n"
        f"🔑 Contraseña reportada: {data.get('pass','')}\n"
        f"📅 Fecha de Compra: {data.get('fecha_compra','')}\n"
        f"🛡️ Garantía: {GARANTIA_DIAS} días\n"
        f"🆔 ID de Compra: {data.get('id_compra','')}\n"
        f"📝 Descripción: {descripcion}\n"
        "-------------------------------\n"
        "El administrador debe revisar esta cuenta. Los reportes tardan de 3-4 días máximo."
    )

    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=reporte_msg, parse_mode="Markdown")
        if foto_id:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=foto_id,
                caption=f"🆔 ID de Compra: {data.get('id_compra','')}\n📝 Descripción: {descripcion}",
                parse_mode="Markdown"
            )
        await update.message.reply_text(
            "✅ Reporte enviado al administrador. Nos pondremos en contacto contigo pronto.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
        )
    except Exception as e:
        logging.error(f"Error al enviar reporte al admin: {e}")
        await update.message.reply_text("❌ Error al enviar el reporte. Por favor, contacta al administrador manualmente.")

    return ConversationHandler.END


async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /responder <ID_USUARIO> <mensaje>
    Permite al administrador enviar un mensaje directo a un cliente.
    """
    try:
        user_id = update.message.from_user.id
    except Exception:
        logging.error("responder: update.message no tiene from_user")
        return

    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❌ Uso: /responder <ID_USUARIO> <mensaje>")
        return

    try:
        target_id = int(context.args[0])
        mensaje = " ".join(context.args[1:]).strip()
        if not mensaje:
            await update.message.reply_text("❌ El mensaje no puede estar vacío.")
            return

        logging.info(f"Administrador {user_id} enviando mensaje a {target_id}: {mensaje[:100]}")
        await context.bot.send_message(
            chat_id=target_id,
            text=f"📩 Mensaje del administrador:\n\n{mensaje}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ Mensaje enviado a {target_id}.")
    except ValueError:
        await update.message.reply_text("❌ ID de usuario inválido. Debe ser un número entero.")
    except Exception as e:
        logging.exception(f"Error al enviar mensaje con /responder: {e}")
        await update.message.reply_text(f"❌ Error al enviar el mensaje: {e}")


async def responder_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Permite al administrador enviar una foto a un cliente usando el caption:
    <ID_USUARIO> <mensaje>
    """
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede enviar fotos a clientes.")
        return

    if not update.message.caption:
        await update.message.reply_text("❌ Debes escribir en el pie de foto: <ID_USUARIO> <mensaje>")
        return

    args = update.message.caption.strip().split()
    if len(args) < 2:
        await update.message.reply_text("❌ Uso correcto: Envía la foto con el pie de foto: <ID_USUARIO> <mensaje>")
        return

    try:
        target_id = int(args[0])
        mensaje = " ".join(args[1:])
        foto_id = update.message.photo[-1].file_id

        await context.bot.send_photo(
            chat_id=target_id,
            photo=foto_id,
            caption=f"📩 Mensaje del administrador:\n\n{mensaje}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ Foto enviada a {target_id}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error al enviar la foto: {e}")


# --- Flujo para agregar combos (solo Admin) ---

async def addcombo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        try:
            await update.message.reply_text("❌ Solo el administrador puede agregar combos.")
        except Exception:
            logging.exception("addcombo_start: fallo reply_text para no-admin")
        return ConversationHandler.END

    context.user_data['nuevo_combo'] = {}

    inicio_text = "📝 Escribe el nombre principal del combo (título):"
    try:
        await update.message.reply_text(inicio_text)
    except Exception:
        logging.exception("addcombo_start: error enviando mensaje de inicio del combo")
        # Intento secundario usando context.bot (puede fallar también)
        try:
            await context.bot.send_message(chat_id=user_id, text=inicio_text)
        except Exception:
            logging.exception("addcombo_start: fallo también con context.bot.send_message — abortando flujo")
            # No podemos comunicarnos con Telegram; abortar conversación de forma segura
            return ConversationHandler.END

    return ADD_COMBO_TITULO

async def addcombo_titulo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nuevo_combo']['titulo'] = update.message.text.strip()
    await update.message.reply_text("✏️ Escribe el subnombre del combo (descripción corta):")
    return ADD_COMBO_SUBNOMBRE

async def addcombo_subnombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nuevo_combo']['subnombre'] = update.message.text.strip()
    await update.message.reply_text("💲 Escribe el precio del combo:")
    return ADD_COMBO_PRECIO

async def addcombo_precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        precio = float(update.message.text.strip().replace(',', '.'))
        if precio <= 0:
            raise ValueError
        context.user_data['nuevo_combo']['precio'] = precio
        context.user_data['nuevo_combo']['plataformas'] = []
        await update.message.reply_text("📺 Escribe la primera plataforma incluida en el combo. Escribe 'listo' cuando termines de agregar plataformas.")
        return ADD_COMBO_PLATAFORMAS
    except ValueError:
        await update.message.reply_text("❌ El precio debe ser un número positivo. Intenta nuevamente:")
        return ADD_COMBO_PRECIO

async def addcombo_plataformas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    if texto.lower() == 'listo':
        combo = context.user_data['nuevo_combo']
        combos.append(combo)
        await update.message.reply_text(
            f"✅ Combo creado:\n*{combo['titulo']}* ({combo['subnombre']})\nPrecio: ${combo['precio']:.2f}\nPlataformas: {', '.join(combo['plataformas'])}",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    else:
        context.user_data['nuevo_combo']['plataformas'].append(texto)
        await update.message.reply_text("Agrega otra plataforma o escribe 'listo' para terminar:")

# Helper: plataformas únicas en stock
def get_stock_platforms():
    """Devuelve las plataformas únicas actualmente en stock, ordenadas."""
    cuentas = cleanup_stock()
    plataformas = []
    for row in cuentas:
        if row and len(row) > 0:
            plat = row[0].strip()
            if plat and plat not in plataformas:
                plataformas.append(plat)
    return sorted(plataformas, key=lambda s: s.lower())

async def addcombo_platform_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja selección toggle de plataformas vía botones durante creación de combo."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = (query.data or "")
    if not data.startswith("addcombo_plat_"):
        return

    plat_encoded = data.replace("addcombo_plat_", "")
    plataforma = plat_encoded.replace("~", " ")

    # Asegurar estructura
    context.user_data.setdefault('nuevo_combo', {})
    context.user_data['nuevo_combo'].setdefault('plataformas', [])

    # Toggle añadido/eliminado
    if plataforma in context.user_data['nuevo_combo']['plataformas']:
        context.user_data['nuevo_combo']['plataformas'].remove(plataforma)
        accion = "eliminada"
    else:
        context.user_data['nuevo_combo']['plataformas'].append(plataforma)
        accion = "añadida"

    # Reconstruir teclado con marcas ✅ para seleccionadas
    plataformas = get_stock_platforms()
    keyboard = []
    for plat in plataformas:
        label = plat
        if plat in context.user_data['nuevo_combo']['plataformas']:
            label = "✅ " + plat
        clean = plat.replace(' ', '~')
        keyboard.append([InlineKeyboardButton(label, callback_data=f"addcombo_plat_{clean}")])

    keyboard.append([InlineKeyboardButton("✅ Finalizar selección", callback_data="addcombo_done")])
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="empezar")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    seleccionadas = context.user_data['nuevo_combo']['plataformas']
    seleccion_text = ", ".join(seleccionadas) if seleccionadas else "(ninguna)"
    texto = f"🔁 Plataforma *{plataforma}* {accion}.\n\nPlataformas seleccionadas: *{seleccion_text}*\n\nPulsa más plataformas o Finalizar selección."

    try:
        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest:
        # Mensaje expirado -> enviar nuevo mensaje
        await context.bot.send_message(chat_id=query.from_user.id, text=texto, reply_markup=reply_markup, parse_mode="Markdown")

    # Mantener la conversación en el mismo estado
    return ADD_COMBO_PLATAFORMAS

async def volver_al_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde al botón '⬅️ Volver al Menú' (callback_data='empezar')."""
    query = getattr(update, "callback_query", None)
    if query:
        try:
            await query.answer()
        except Exception:
            pass
    await show_main_menu(update, context)

# Handler para el botón "Volver al Menú" (callback_data="empezar")
async def addcombo_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finaliza el combo desde el botón 'Finalizar'."""
    query = update.callback_query
    if query:
        await query.answer()

    combo = context.user_data.get('nuevo_combo', {})
    plataformas = combo.get('plataformas', [])

    if not plataformas:
        if query:
            try:
                await query.edit_message_text("❌ No has seleccionado ninguna plataforma. Selecciona al menos una antes de finalizar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver", callback_data="empezar")]]))
            except BadRequest:
                await context.bot.send_message(chat_id=query.from_user.id, text="❌ No has seleccionado ninguna plataforma. Selecciona al menos una antes de finalizar.")
        else:
            await context.bot.send_message(chat_id=update.effective_user.id, text="❌ No has seleccionado ninguna plataforma.")
        return ADD_COMBO_PLATAFORMAS

    combos.append(combo)
    save_combos_csv()  # Persistir al crear por botones

    texto_confirm = f"✅ Combo creado:\n*{combo.get('titulo','Sin título')}* ({combo.get('subnombre','')})\nPrecio: ${combo.get('precio',0.0):.2f}\nPlataformas: {', '.join(plataformas)}"
    try:
        if query:
            await query.edit_message_text(texto_confirm, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=update.effective_user.id, text=texto_confirm, parse_mode="Markdown")
    except BadRequest:
        await context.bot.send_message(chat_id=update.effective_user.id, text=texto_confirm)

    # Limpiar estado y terminar
    context.user_data.pop('nuevo_combo', None)
    return ConversationHandler.END

async def show_combos_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la lista de combos y botones para comprar (callback `comprar_combo_{i}`)."""
    keyboard = []
    mensaje = "🎁 *Combos disponibles:*\n\n"

    if not combos:
        mensaje = "❌ No hay combos disponibles en este momento."
        keyboard = [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="empezar")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if getattr(update, "callback_query", None):
            query = update.callback_query
            try:
                await query.answer()
            except Exception:
                pass
            try:
                await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode="Markdown")
            except Exception:
                await context.bot.send_message(chat_id=query.from_user.id, text=mensaje, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(mensaje, reply_markup=reply_markup, parse_mode="Markdown")
        return

    for i, combo in enumerate(combos):
        titulo = combo.get('titulo', 'Sin título')
        sub = combo.get('subnombre', '')
        precio = float(combo.get('precio', 0.0))
        plataformas = combo.get('plataformas', [])
        mensaje += f"{i+1}. *{titulo}* ({sub}) - ${precio:.2f}\n"
        mensaje += "   Plataformas: " + (", ".join(plataformas) if plataformas else "(no definidas)") + "\n"
        keyboard.append([InlineKeyboardButton(f"Comprar {titulo}", callback_data=f"comprar_combo_{i}")])

    keyboard.append([InlineKeyboardButton("⬅️ Volver al menú", callback_data="empezar")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if getattr(update, "callback_query", None):
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(chat_id=query.from_user.id, text=mensaje, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(mensaje, reply_markup=reply_markup, parse_mode="Markdown")

# Handler para mostrar combos en el menú principal
async def handle_comprar_combo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa la compra de un combo identificado por callback_data `comprar_combo_{i}`."""
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    data = (query.data or "")
    if not data.startswith("comprar_combo_"):
        await query.edit_message_text("❌ Callback inválido para comprar combo.")
        return

    try:
        idx = int(data.split("_")[-1])
    except Exception:
        await query.edit_message_text("❌ Índice de combo inválido.")
        return

    if idx < 0 or idx >= len(combos):
        await query.edit_message_text("❌ Combo no encontrado.")
        return

    combo = combos[idx]
    plataformas = combo.get('plataformas', [])
    precio_combo = float(combo.get('precio', 0.0))

    if not plataformas:
        await query.edit_message_text("❌ Este combo no tiene plataformas definidas.")
        return

    user_id = query.from_user.id
    inicializar_usuario(user_id)

    prev_balance = clientes.get(user_id, 0.0)
    if prev_balance < precio_combo:
        await query.edit_message_text(f"❌ Saldo insuficiente. Necesitas ${precio_combo:.2f} y tienes ${prev_balance:.2f}.")
        return

    simulated = load_stock()
    selects = []  # (plataforma, tipo, precio) elegidos
    for plat in plataformas:
        plat_lower = plat.strip().lower()
        found = False
        for i, row in enumerate(simulated):
            if not row or len(row) < 5:
                continue
            row_platform = (row[0] or "").strip().lower()
            if row_platform != plat_lower:
                continue
            if len(row) >= 7:
                try:
                    perfiles_disponibles = int(row[5])
                except Exception:
                    continue
                if perfiles_disponibles <= 0:
                    continue
                precio = 0.0
                try:
                    precio = float(str(row[4]).strip())
                except Exception:
                    pass
                selects.append((row[0].strip(), row[1].strip(), precio))
                if perfiles_disponibles > 1:
                    simulated[i][5] = str(perfiles_disponibles - 1)
                    if len(simulated[i]) > 6 and simulated[i][6].isdigit():
                        simulated[i][6] = str(int(simulated[i][6]) + 1)
                    else:
                        while len(simulated[i]) < 7:
                            simulated[i].append("1")
                        simulated[i][6] = "2"
                else:
                    del simulated[i]
                found = True
                break
            else:
                precio = 0.0
                try:
                    precio = float(str(row[4]).strip())
                except Exception:
                    pass
                selects.append((row[0].strip(), row[1].strip(), precio))
                del simulated[i]
                found = True
                break
        if not found:
            no_stock_text = f"❌ Lo siento, ya no hay stock de *{plat}* para completar este combo."
            back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
            await query.edit_message_text(no_stock_text, reply_markup=back_markup, parse_mode="Markdown")
            return

    entregados = []
    for platform, tipo, precio_item in selects:
        res = entregar_cuenta(platform, tipo, precio_item)
        if not res:
            await query.edit_message_text("❌ No se pudo completar la compra por cambio de stock. Intenta de nuevo.")
            return
        entregados.append(res)

    # Descontar saldo total y persistir
    clientes[user_id] -= precio_combo
    guardar_clientes()
    remaining = clientes[user_id]

    id_compra = str(uuid.uuid4()).split('-')[0].upper()
    n_items = len(entregados)
    precio_por_item = round(precio_combo / n_items, 2) if n_items else 0.0

    for entrega in entregados:
        plat_entregado, plan_entregado, correo, password, _, perfil_entregado = entrega
        plan_lower = (plan_entregado or "").lower()
        if 'perfil' in plan_lower and 'completa' not in plan_lower:
            display_plan = 'perfil'
        elif 'completa' in plan_lower and 'perfil' not in plan_lower:
            display_plan = 'completa'
        else:
            display_plan = plan_entregado or 'otro'

        # Ajuste: para cuentas completas mostrar "Todos los perfiles" y
        # "1 dispositivo por perfil"; para perfiles individuales mantener "Perfil N" y "1 dispositivo".
        if perfil_entregado == 0:
            perfil_text = "Todos los perfiles"
            dispositivos_text = "1 dispositivo por perfil"
        else:
            perfil_text = f"Perfil {perfil_entregado}"
            dispositivos_text = "1 dispositivo"

        mensaje += (
            f"• {plat_entregado} — {display_plan} — {perfil_text}\n"
            f"   Dispositivos: {dispositivos_text}\n"
            f"   Correo: `{correo}`\n"
            f"   Contraseña: `{password}`\n\n"
        )
    for entrega in entregados:
        plat_entregado, plan_entregado, correo, password, _, perfil_entregado = entrega
        plan_lower = (plan_entregado or "").lower()
        if 'perfil' in plan_lower and 'completa' not in plan_lower:
            display_plan = 'perfil'
        elif 'completa' in plan_lower and 'perfil' not in plan_lower:
            display_plan = 'completa'
        else:
            display_plan = plan_entregado or 'otro'

        if perfil_entregado == 0:
            perfil_text = "Cuenta Completa"
            dispositivos_text = "Todos los dispositivos"
        else:
            perfil_text = f"Perfil {perfil_entregado}"
            dispositivos_text = "1 dispositivo"

        mensaje += (
            f"• {plat_entregado} — {display_plan} — {perfil_text}\n"
            f"   Dispositivos: {dispositivos_text}\n"
            f"   Correo: `{correo}`\n"
            f"   Contraseña: `{password}`\n\n"
        )

    # Añadir garantía, descuento y saldo restante
    mensaje += f"🛡️ Garantía: {GARANTIA_DIAS} días\n\n"
    mensaje += f"🔻 Se descontó: ${precio_combo:.2f}\n"
    mensaje += f"💳 Saldo restante: ${remaining:.2f}\n\n"
    mensaje += "¡Gracias por tu compra! Guarda el ID de compra para cualquier reporte."

    try:
        await context.bot.send_message(chat_id=user_id, text=mensaje, parse_mode="Markdown")
        try:
            await query.edit_message_text("✅ Compra realizada. Revisa tu chat privado para los detalles.")
        except Exception:
            pass
    except Exception:
        try:
            await query.edit_message_text("✅ Compra realizada. Revisa tu chat privado para los detalles.")
        except Exception:
            pass

    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Combo vendido: {combo.get('titulo')} a {user_id} | ID {id_compra}")
    except Exception:
        logging.debug("No se pudo notificar al admin sobre la venta del combo.")

async def ver_clientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/verclientes - Muestra la lista de clientes con su ID y saldo (solo Admin)."""
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return

    if not clientes:
        await update.message.reply_text("No hay clientes registrados.")
        return

    mensaje = "🗂️ *Lista de clientes:*\n\n"
    for cid, saldo in clientes.items():
        mensaje += f"ID: `{cid}` | Saldo: ${saldo:.2f}\n"
    await update.message.reply_text(mensaje, parse_mode="Markdown")

# Persistir inmediatamente cuando se crea un combo (texto)
async def addcombo_plataformas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    if texto.lower() == 'listo':
        combo = context.user_data.get('nuevo_combo', {})
        combos.append(combo)
        save_combos_csv()  # Persistir al crear por texto
        await update.message.reply_text(
            f"✅ Combo creado:\n*{combo.get('titulo','Sin título')}* ({combo.get('subnombre','')})\nPrecio: ${float(combo.get('precio',0.0)):.2f}\nPlataformas: {', '.join(combo.get('plataformas',[]))}",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    else:
        context.user_data.setdefault('nuevo_combo', {})
        context.user_data['nuevo_combo'].setdefault('plataformas', [])
        context.user_data['nuevo_combo']['plataformas'].append(texto)
        await update.message.reply_text("Agrega otra plataforma o escribe 'listo' para terminar:")

async def reporte_fecha_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 4: Recibe la fecha, la normaliza a DD/MM/AAAA y pide la descripción del problema."""
    user_id = update.message.from_user.id
    fecha_input = (update.message.text or "").strip()

    # Intentar normalizar/parsear distintos formatos automáticos
    fecha_norm = _normalize_fecha_input(fecha_input)
    if not fecha_norm:
        await update.message.reply_text(
            "❌ Fecha inválida. Envía la fecha en formato DD/MM/AAAA.\n"
            "Ejemplos válidos: `01/01/2025`, `01012025`, `01-01-2025`.\n"
            "Intenta nuevamente:",
            parse_mode="Markdown"
        )
        return REPORTE_FECHA

    # Guardar fecha normalizada
    tmp_reporte[user_id]['fecha_compra'] = fecha_norm

    await update.message.reply_text(
        "📝 Describe detalladamente el problema que presenta la cuenta. Si tienes una captura de pantalla, ¡puedes enviarla ahora mismo junto con tu texto!",
        parse_mode="Markdown"
    )
    return REPORTE_DESCRIPCION



async def reporte_pass_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 3: Recibe y valida la contraseña."""
    user_id = update.message.from_user.id
    password = update.message.text.strip()
    if not password:
        await update.message.reply_text("❌ La contraseña no puede estar vacía. Intenta nuevamente:")
        return REPORTE_PASS

    tmp_reporte[user_id]['pass'] = password

    # Instrucción clara y ejemplo práctico; indicar que puede escribir sin '/' y se formatea
    await update.message.reply_text(
        "Ingresa la fecha en que compraste esta cuenta (Formato: DD/MM/AAAA).\n"
        "Puedes escribir por ejemplo: 01/01/2025 o 01012025 — la fecha se normalizará automáticamente.",
        parse_mode="Markdown"
    )
    return REPORTE_FECHA

async def reporte_correo_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 2: Recibe el correo del reporte. Valida formato básico de email."""
    user_id = update.message.from_user.id
    correo = update.message.text.strip()
    if not correo:
        await update.message.reply_text("❌ El correo no puede estar vacío. Intenta nuevamente:")
        return REPORTE_CORREO

    # Validación simple de correo (básica, no exhaustiva)
    if not re.match(r"[^@]+@[^@]+\.[^@]+", correo):
        await update.message.reply_text("❌ Formato de correo inválido. Ejemplo válido: usuario@dominio.com. Intenta nuevamente:")
        return REPORTE_CORREO

    tmp_reporte[user_id]['correo'] = correo
    await update.message.reply_text("Ingresa la contraseña de la cuenta:", parse_mode="Markdown")
    return REPORTE_PASS

async def eliminar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/eliminarcliente <ID> - Elimina un cliente del archivo de clientes (solo Admin).
    No toca otros archivos ni código."""
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede usar este comando.")
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("❌ Uso: /eliminarcliente <ID_USUARIO>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID inválido. Debe ser un número entero.")
        return

    if target_id not in clientes:
        await update.message.reply_text(f"❌ El cliente con ID {target_id} no existe en el registro.")
        return

    # Eliminar del dict y persistir
    try:
        del clientes[target_id]
        guardar_clientes()
        await update.message.reply_text(f"✅ Cliente ID {target_id} eliminado de {CSV_CLIENTES}. No se borró ningún otro archivo ni código.")
        # opcional: notificar al usuario (intento silencioso)
        try:
            await context.bot.send_message(chat_id=target_id, text="⚠️ Tu cuenta de cliente ha sido eliminada por el administrador.")
        except Exception:
            logging.debug(f"No se pudo notificar al cliente {target_id} sobre su eliminación.")
    except Exception as e:
        logging.exception(f"Error eliminando cliente {target_id}: {e}")
        await update.message.reply_text("❌ Error al intentar eliminar el cliente. Revisa los logs.")

    # Aquí podríamos preguntar si se desea eliminar también el historial de compras...
    # pero eso podría ser destructivo. Mejor que el admin lo haga manualmente si es necesario.

def main():
    """Configuración principal del bot y registro de handlers."""
    cargar_clientes()
    load_combos_csv()

    # Eliminar webhook si existe (solo para polling, no afecta a webhooks en producción)
    try:
        Bot(TOKEN).delete_webhook()
        logging.info("Webhook eliminado (si existía).")
    except Exception as e:
        logging.warning(f"No se pudo eliminar webhook: {e}")

    application = ApplicationBuilder().token(TOKEN).build()


    # Conversation handler: combos
    addcombo_handler = ConversationHandler(
        entry_points=[CommandHandler('addcombo', addcombo_start)],
        states={
            ADD_COMBO_TITULO: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_titulo)],
            ADD_COMBO_SUBNOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_subnombre)],
            ADD_COMBO_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_precio)],
            ADD_COMBO_PLATAFORMAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_plataformas)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(addcombo_handler)

        # Comandos generales
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("saldo", saldo))
    application.add_handler(CommandHandler("comandos", comandos))
    application.add_handler(CommandHandler("historial", historial))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("combos", show_combos_menu))
    application.add_handler(CommandHandler("verclientes", ver_clientes))
    ...
    # Navegación y compra
    application.add_handler(CallbackQueryHandler(show_combos_menu, pattern='^show_combos_menu$'))
    application.add_handler(CallbackQueryHandler(show_categories, pattern='^show_categories$'))
    application.add_handler(CallbackQueryHandler(show_plataformas, pattern='^category_(completa|perfil)$'))
    application.add_handler(CallbackQueryHandler(handle_platform_selection, pattern='^select_(completa|perfil)_.*'))
    application.add_handler(CallbackQueryHandler(handle_compra_final, pattern='^buy_.*'))
    # Handler para comprar combos (cada botón produce comprar_combo_{i})
    application.add_handler(CallbackQueryHandler(handle_comprar_combo, pattern=r'^comprar_combo_\d+$'))

    # Comandos admin
    application.add_handler(CommandHandler("stock", stock_check))
    application.add_handler(CommandHandler("recargar", recargar))
    application.add_handler(CommandHandler("quitarsaldo", quitar_saldo))
    application.add_handler(CommandHandler("consultarsaldo", consultar_saldo))
    application.add_handler(CommandHandler("responder", responder))
    application.add_handler(CommandHandler("eliminarcliente", eliminar_cliente))
    application.add_handler(CommandHandler("borrarventa", borrar_venta))

        # Conversation handler: combos
    addcombo_handler = ConversationHandler(
        entry_points=[CommandHandler('addcombo', addcombo_start)],
        states={
            ADD_COMBO_TITULO: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_titulo)],
            ADD_COMBO_SUBNOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_subnombre)],  # CORRECCIÓN: nombre correcto
            ADD_COMBO_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_precio)],
            # En el estado de plataformas aceptamos texto y callbacks (botones de stock)
            ADD_COMBO_PLATAFORMAS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addcombo_plataformas),
                CallbackQueryHandler(addcombo_platform_callback, pattern=r'^addcombo_plat_.*'),
                CallbackQueryHandler(addcombo_finish_callback, pattern=r'^addcombo_done$'),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(addcombo_handler)

    # Flujo reporte (Conversation)
    reporte_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(reporte_start, pattern='^iniciar_reporte$')],
        states={
            REPORTE_ID_COMPRA: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_id_compra_recibida)],
            REPORTE_CORREO: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_correo_recibida)],
            REPORTE_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_pass_recibida)],
            REPORTE_FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_fecha_recibida)],
            REPORTE_DESCRIPCION: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, reporte_descripcion_recibida)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(reporte_handler)

        # Flujo agregar venta (Conversation)
    addventa_handler = ConversationHandler(
        entry_points=[CommandHandler('addventa', addventa)],
        states={
            AGREGAR_TIPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_tipo)],
            AGREGAR_PERFILES: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_perfiles)],
            AGREGAR_CORREO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_correo)],
            AGREGAR_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_pass)],
            AGREGAR_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_precio)],
            AGREGAR_MATERIAL: [MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, guardar_material_perfil)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(addventa_handler)

     # Navegación y compra
    application.add_handler(CallbackQueryHandler(show_combos_menu, pattern='^show_combos_menu$'))
    application.add_handler(CallbackQueryHandler(show_categories, pattern='^show_categories$'))
    application.add_handler(CallbackQueryHandler(show_plataformas, pattern='^category_(completa|perfil)$'))
    application.add_handler(CallbackQueryHandler(handle_platform_selection, pattern='^select_(completa|perfil)_.*'))
    application.add_handler(CallbackQueryHandler(handle_compra_final, pattern='^buy_.*'))
    # Handler para comprar combos (cada botón produce comprar_combo_{i})
    application.add_handler(CallbackQueryHandler(handle_comprar_combo, pattern=r'^comprar_combo_\d+$'))
     # Mostrar información de recarga (botón del menú)
    application.add_handler(CallbackQueryHandler(show_recarga_info, pattern=r'^mostrar_recarga$'))
    # Volver al menú (botón "empezar")
    application.add_handler(CallbackQueryHandler(volver_al_menu_callback, pattern=r'^empezar$'))

    # Borrado / respuestas admin
    application.add_handler(CallbackQueryHandler(borrar_venta, pattern='^borrar_venta_menu$'))
    application.add_handler(CallbackQueryHandler(mostrar_lista_borrar, pattern='^borrar_(completa|perfil|otro)$'))
    application.add_handler(MessageHandler(filters.Regex(r'^\d+$') & filters.Chat(ADMIN_ID), borrar_stock_por_indice))
    application.add_handler(MessageHandler(filters.PHOTO & filters.Chat(ADMIN_ID), responder_foto))

    # Ejecutar
    application.run_polling()

if __name__ == '__main__':
    main()
