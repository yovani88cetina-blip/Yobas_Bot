from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- Configuración y Almacenamiento de Datos ---
# NOTA: Cambia este token por el real si lo vas a ejecutar
TOKEN = "8457617126:AAFtla_bwdiw78zpH70z8W8sS9ICBFK3YFU"  

# 🚨 ID del Administrador FIJA
ADMIN_ID = 7006777962 

# Archivos de persistencia
CSV_CLIENTES = 'clientes.csv'
STOCK_FILE = 'stock.csv'
COMPRAS_FILE = 'compras_global.csv' 

# Variables globales para el estado
clientes = {}
tmp_venta = {} # Usado para /addventa
tmp_reporte = {} # Usado para el flujo de Reporte
ADMIN_USERNAME = "YobasAdmin" # Nombre de referencia
ADMIN_PHONE = "9992779422"
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
    """/addventa - Punto de entrada para el flujo de adición de stock (solo Admin)."""
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede agregar ventas.")
        return ConversationHandler.END

    if not context.args:
        await update.message.reply_text("❌ Uso: /addventa <nombre de la plataforma>\nEj: /addventa Netflix")
        return ConversationHandler.END

    plataforma = " ".join(context.args).strip()
    if not plataforma:
        await update.message.reply_text("❌ El nombre de la plataforma no puede estar vacío.")
        return ConversationHandler.END

    tmp_venta[user_id] = {"plataforma": plataforma}
    await update.message.reply_text(
        f"Añadiendo {plataforma}.\nResponde con el tipo de cuenta (Ej: 'Completa', 'Perfil 1'):",
        parse_mode="Markdown"
    )
    return AGREGAR_TIPO

async def venta_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1: Recibe y valida el tipo de cuenta (Completa/Perfil)."""
    user_id = update.message.from_user.id
    tipo = update.message.text.strip()
    if not tipo:
        await update.message.reply_text("❌ El tipo de cuenta no puede estar vacío. Intenta nuevamente:")
        return AGREGAR_TIPO
    
    tipo_lower = tipo.lower()
    
    # Si contiene 'perfil' y NO 'completa', ir a preguntar perfiles (aunque ya esté especificado, por si acaso).
    if 'perfil' in tipo_lower and 'completa' not in tipo_lower and 'perfil' not in tmp_venta[user_id].get('plataforma', '').lower():
        tmp_venta[user_id]['tipo_base'] = tipo
        await update.message.reply_text("¿Cuántos perfiles tiene esta cuenta (solo el número)?")
        return AGREGAR_PERFILES
    else:
        # Es completa u otro tipo
        tmp_venta[user_id]['tipo'] = tipo
        await update.message.reply_text("Ingresa el correo de la cuenta:")
        return AGREGAR_CORREO

async def venta_perfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1.5: Recibe el número de perfiles si se seleccionó 'Perfil'."""
    user_id = update.message.from_user.id
    try:
        num_perfiles = int(update.message.text.strip())
        if num_perfiles <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Ingresa un número de perfiles válido (ej: 1, 4, 5).")
        return AGREGAR_PERFILES
    
    # Construir el tipo final de cuenta
    base_tipo = tmp_venta[user_id].get('tipo_base', 'Perfil')
    tmp_venta[user_id]['tipo'] = f"{base_tipo} ({num_perfiles})"
    
    await update.message.reply_text("Ingresa el correo de la cuenta:")
    return AGREGAR_CORREO

async def venta_correo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 2: Recibe y valida el correo."""
    user_id = update.message.from_user.id
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

    with open(STOCK_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        # Nuevo formato: plataforma, tipo, correo, pass, precio, perfiles_disponibles, perfil_actual
        writer.writerow([data['plataforma'], tipo, data['correo'], data['pass'], f"{data['precio']:.2f}", perfiles, 1])

    await update.message.reply_text(
        f"✅ Se añadió una cuenta de {data['plataforma']} ({tipo}) con {perfiles} perfiles disponibles, precio ${data['precio']:.2f} cada uno.",
        parse_mode="Markdown"
    )
    tmp_venta.pop(user_id)
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
    )
    cliente_comandos = (
        "👤 *Comandos de Cliente:*\n"
        "/start - Iniciar el bot y ver el menú principal.\n"
        "/saldo - Ver tu saldo actual.\n"
        "/comandos - Muestra esta lista.\n"
        "/historial - Descarga tu historial de compras.\n"
        "/cancel - Cancela un flujo de conversación (si está activo).\n"
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
    try:
        if WELCOME_IMAGE and image_path.exists():
            file_size = image_path.stat().st_size
            logging.info(f"Start: imagen encontrada (tamaño {file_size} bytes). Intentando enviar como photo...")
            with open(image_path, "rb") as img:
                try:
                    # Intento principal: enviar como photo
                    if getattr(update, "message", None):
                        await update.message.reply_photo(photo=img, caption=bienvenida_text, parse_mode="Markdown")
                    else:
                        await context.bot.send_photo(chat_id=user_id, photo=img, caption=bienvenida_text, parse_mode="Markdown")
                except Exception as e_photo:
                    logging.warning(f"Fallo send_photo: {e_photo}. Intentando enviar como documento...")
                    # Reabrir el archivo y enviar como documento (fall back)
                    with open(image_path, "rb") as doc:
                        if getattr(update, "message", None):
                            await update.message.reply_document(document=doc, caption=bienvenida_text, parse_mode="Markdown")
                        else:
                            await context.bot.send_document(chat_id=user_id, document=doc, caption=bienvenida_text, parse_mode="Markdown")
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
        "/addventa <Plataforma> - Inicia el flujo para agregar una cuenta al stock.\n"
        "/borrarventa - Inicia el flujo para eliminar una cuenta del stock.\n"
        "/recargar <ID> <monto> - Recarga saldo a un usuario.\n"
        "/quitarsaldo <ID> <monto> - Descuenta saldo a un usuario.\n"
        "/consultarsaldo <ID> - Consulta el saldo de un usuario específico.\n"
        "/historial - Obtén el CSV de tu historial de compras.\n"
        "/cancel - Cancela un flujo de conversación (e.g., /addventa, /borrarventa o Reporte).\n"
        "/addcombo - Inicia el flujo para agregar un nuevo combo de cuentas.\n"
        "/combos - Muestra los combos disponibles para compra.\n"
        "/verclientes - Muestra la lista de clientes con su ID y saldo.\n"
        "/responder <ID> <mensaje> - Responde a reportes o envía mensajes a clientes.\n"
    )
    cliente_comandos = (
        "👤 *Comandos de Cliente:*\n"
        "------------------------------------\n"
        "/start - Iniciar el bot y ver el menú principal.\n"
        "/saldo - Ver tu saldo actual.\n"
        "/comandos - Muestra esta lista.\n"
        "/historial - Obtén el CSV con el historial de tus compras.\n"
        "/cancel - Cancela un flujo de conversación (si está activo).\n"
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

    stock_info = get_dynamic_stock_info()
    
    if not stock_info:
        await update.message.reply_text("📦 El inventario está vacío.")
        return

    message = "📦 Inventario Actual:\n"
    stock_list = load_stock()
    
    counts = defaultdict(int)
    for row in stock_list:
        # soportar filas con 5 o 7 campos
        if len(row) >= 5:
            try:
                platform = row[0].strip()
                tipo = row[1].strip()
                precio = float(row[4])
                key = (platform, tipo, precio)
                counts[key] += 1
            except (ValueError, IndexError):
                continue

    sorted_keys = sorted(counts.keys())
    
    current_platform = None
    for platform, tipo, precio in sorted_keys:
        count = counts[(platform, tipo, precio)]
        
        if platform != current_platform:
            message += f"\n*--- {platform.upper()} ---*\n"
            current_platform = platform

        message += f"▪️ {tipo} - ${precio:.2f} (Disponibles: {count})\n"
        
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
    """Registra la compra del usuario en su archivo de historial CSV."""
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
    """Entrega el siguiente perfil disponible y actualiza el stock."""
    cuentas = load_stock()
    for i, row in enumerate(cuentas):
        # Si la cuenta tiene campo de perfiles
        if len(row) == 7:
            stock_plataforma, stock_tipo, correo, password, stock_precio, perfiles_disponibles, perfil_actual = row
            try:
                stock_precio = float(stock_precio)
                perfiles_disponibles = int(perfiles_disponibles)
                perfil_actual = int(perfil_actual)
            except ValueError:
                continue

            if stock_plataforma.strip() == plataforma.strip() and \
               stock_tipo.strip() == tipo.strip() and \
               abs(stock_precio - precio_buscado) < 0.01 and \
               perfiles_disponibles > 0:

                # Entregar el perfil actual
                perfil_entregado = perfil_actual

                # Actualizar el stock
                if perfiles_disponibles > 1:
                    cuentas[i][5] = str(perfiles_disponibles - 1)
                    cuentas[i][6] = str(perfil_actual + 1)
                else:
                    # Si ya no quedan perfiles, eliminar la cuenta
                    del cuentas[i]

                save_stock(cuentas)
                # Devuelve la cuenta y el número de perfil entregado
                return [stock_plataforma, stock_tipo, correo, password, stock_precio, perfil_entregado]
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
            
    # 3. Guardar el stock actualizado
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
    await query.answer()

    stock_info = get_dynamic_stock_info()
    
    has_completa = 'completa' in stock_info and stock_info['completa']
    has_perfil = 'perfil' in stock_info and stock_info['perfil']

    if not has_completa and not has_perfil:
        await query.edit_message_text(
            "❌ No hay stock disponible en este momento. Vuelve más tarde.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
        )
        return

    keyboard = []
    if has_completa:
        keyboard.append([InlineKeyboardButton("🥇 Cuentas Completas", callback_data="category_completa")])
    if has_perfil:
        keyboard.append([InlineKeyboardButton("👥 Cuentas por Perfil", callback_data="category_perfil")])

    keyboard.append([InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✅ Cuentas disponibles:\n\nSelecciona si quieres Perfiles o Completas:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def show_plataformas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra las plataformas disponibles DENTRO de la categoría seleccionada.
    """
    query = update.callback_query
    await query.answer()
    
    category = query.data.replace('category_', '') # 'completa' o 'perfil'
    
    stock_info = get_dynamic_stock_info()
    platforms_in_category = stock_info.get(category, {})
    logging.info(f"platforms_in_category -> {platforms_in_category}")
    if not platforms_in_category:
        await query.edit_message_text(
            f"❌ No hay stock de {category.capitalize()} disponible en este momento.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="show_categories")]])
        )
        return

    keyboard = []
    for platform, data in sorted(platforms_in_category.items()):
        # Precio más bajo para referencia
        precio_min = data['precio']
        # El callback_data debe llevar a la selección final del tipo/precio
        # Guardamos la categoría y la plataforma
        # Usamos .replace(' ', '~') para las plataformas con espacios, para que funcione en callback_data
        clean_platform = platform.replace(' ', '~')
        keyboard.append([
            InlineKeyboardButton(
                f"▶️ {platform} (Desde ${precio_min:.2f})", 
                callback_data=f"select_{category}_{clean_platform}"
            )
        ])
        
    keyboard.append([InlineKeyboardButton("⬅️ Volver a Categorías", callback_data="show_categories")])
    
    await query.edit_message_text(
        f"✅ {category.capitalize()} Disponibles:\n\nSelecciona una plataforma:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

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
        stock_platform, stock_tipo, _, _, stock_precio_str = row
        stock_tipo_lower = stock_tipo.lower()
        is_completa = 'completa' in stock_tipo_lower and 'perfil' not in stock_tipo_lower
        is_perfil = 'perfil' in stock_tipo_lower
        current_category = ''
        if is_completa:
            current_category = 'completa'
        elif is_perfil:
            current_category = 'perfil'
        if stock_platform.strip().lower() == platform.strip().lower() and current_category == category:
            try:
                stock_precio = float(stock_precio_str)
                clean_platform = platform.replace(' ', '~')
                clean_type = stock_tipo.replace(' ', '~')
                callback_data = f"buy_{category}_{clean_platform}_{clean_type}_{stock_precio}"
                
                fake_query = SimpleNamespace()
                fake_query.data = callback_data
                fake_query.from_user = query.from_user
                fake_query.answer = query.answer
                fake_query.message = query.message

                fake_update = SimpleNamespace()
                fake_update.callback_query = fake_query

                await handle_compra_final(fake_update, context, callback_data=callback_data)
                return
            except ValueError:
                continue
    # Si no hay stock
    await query.edit_message_text(
        f"❌ No hay stock disponible para {platform} en este momento.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver a Plataformas", callback_data=f"category_{category}")]])
    )


async def handle_compra_final(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data=None):
    query = update.callback_query
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

    if clientes[user_id] < precio_final:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Saldo insuficiente. Necesitas ${precio_final:.2f} y solo tienes ${clientes[user_id]:.2f}.",
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

    # Generar ID de Compra
    id_compra = str(uuid.uuid4()).split('-')[0].upper() # Genera un ID corto y aleatorio

    # Log de la compra
    log_compra(user_id, plan_entregado, correo, password, precio_final, id_compra)

    # 4. Enviar cuenta al usuario (NUEVO MENSAJE)
    logging.info(f"Entrega preparada: cuenta_data={cuenta_data}, user_id={user_id}, precio={precio_final:.2f}, saldo_restante={clientes[user_id]:.2f}, id_compra={id_compra}")

    mensaje_entrega = (
        "🎉 ¡Tu cuenta ha sido entregada! 🎉\n"
        "--------------------------------------\n"
        f"➡️ Plataforma: {platform}\n"
        f"➡️ Tipo: {plan_entregado}\n"
        f"➡️ Correo: {correo}\n"
        f"➡️ Contraseña: {password}\n"
        f"➡️ Perfil asignado: Perfil {perfil_entregado}\n"
        f"➡️ Costo: ${precio_final:.2f}\n"
        "--------------------------------------\n"
        f"🛡️ Garantía: {GARANTIA_DIAS} días\n"
        f"🆔 ID de Compra: {id_compra}\n"
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
                    caption=f"Material para tu Perfil {perfil_entregado}"
                )
        except Exception as e:
            logging.exception(f"No se pudo enviar material '{material_filename}' a {user_id}: {e}")
            # No abortar; la compra ya está registrada

    # 5. Abrir automáticamente el menú principal (NUEVO MENSAJE)
    await show_main_menu(update, context, welcome_msg="✅ Compra exitosa. ¿Qué deseas hacer ahora?")


# --- Funciones de Menú y Redirección ---

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
        
async def show_recarga_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la información de recarga y el botón para volver."""
    user_id = update.effective_user.id
    await update.callback_query.answer()
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
    await update.callback_query.edit_message_text(
        f"💰 Tu saldo actual es: ${clientes[user_id]:.2f}\n\n"
        f"Para recargar, contacta al administrador e indica tu ID de usuario: {user_id}.",
        reply_markup=back_keyboard,
        parse_mode="Markdown"
    )

# Nueva configuración de contacto / bienvenida (coloca aquí tu número e imagen)
ADMIN_PHONE = ""  # Ej: "+52 844 212 5550"  <- Rellena con tu número
WELCOME_IMAGE = "welcome_bot.jpg"  # Nombre del archivo de la imagen de bienvenida (colócala en el mismo directorio)

# --- Lógica de Validación de ID de Compra ---
def validar_id_compra(user_id: int, id_compra: str) -> bool:
    """Verifica si el ID de compra fue emitido al user_id proporcionado."""
    try:
        with open(COMPRAS_FILE, 'r') as f:
            reader = csv.reader(f)
            # Saltar encabezado
            next(reader, None)
            for row in reader:
                # Formato: ['ID_Compra', 'ID_Usuario', 'Fecha', 'Plan', 'Correo', 'Contraseña', 'Precio']
                if len(row) > 1 and row[0].strip() == id_compra.strip() and int(row[1]) == user_id:
                    return True
    except FileNotFoundError:
        logging.warning(f"{COMPRAS_FILE} no existe.")
    except Exception as e:
        logging.error(f"Error al leer {COMPRAS_FILE}: {e}")
        
    return False

# --- Flujo de Reporte (Conversación) ---

async def reporte_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Punto de entrada para el flujo de reporte."""
    user_id = update.effective_user.id
    tmp_reporte[user_id] = {}
    
    await update.callback_query.edit_message_text(
        "📝 Inicio del Reporte\n\n"
        "Ingresa el ID de Compra de la cuenta que presenta problemas (lo encuentras en el mensaje de entrega):",
        parse_mode="Markdown"
    )
    return REPORTE_ID_COMPRA

async def reporte_id_compra_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1: Recibe el ID de Compra y lo valida."""
    user_id = update.message.from_user.id
    id_compra = update.message.text.strip().upper()
    
    if not id_compra or len(id_compra) < 4:
        await update.message.reply_text("❌ El ID de Compra es muy corto o está vacío. Intenta nuevamente:")
        return REPORTE_ID_COMPRA
        
    # VALIDACIÓN CLAVE
    if not validar_id_compra(user_id, id_compra):
        await update.message.reply_text(
            f"❌ ID de Compra inválido: El ID {id_compra} no se encuentra en tu historial de compras o no te pertenece. "
            "Verifica que el ID sea correcto e inténtalo de nuevo, o /cancel.",
            parse_mode="Markdown"
        )
        return REPORTE_ID_COMPRA
        
    tmp_reporte[user_id]['id_compra'] = id_compra
    
    await update.message.reply_text("✅ ID Validado. Ahora ingresa el correo de la cuenta:", parse_mode="Markdown")
    return REPORTE_CORREO

async def reporte_correo_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 2: Recibe el correo del reporte."""
    user_id = update.message.from_user.id
    correo = update.message.text.strip()
    if not correo:
        await update.message.reply_text("❌ El correo no puede estar vacío. Intenta nuevamente:")
        return REPORTE_CORREO
        
    tmp_reporte[user_id]['correo'] = correo

    await update.message.reply_text("Ingresa la contraseña de la cuenta:", parse_mode="Markdown")
    return REPORTE_PASS

async def reporte_pass_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 3: Recibe la contraseña del reporte."""
    user_id = update.message.from_user.id
    password = update.message.text.strip()
    if not password:
        await update.message.reply_text("❌ La contraseña no puede estar vacía. Intenta nuevamente:")
        return REPORTE_PASS
        
    tmp_reporte[user_id]['pass'] = password
    
    await update.message.reply_text(
        "Ingresa la fecha en que compraste esta cuenta (Formato: DD/MM/AAAA):",
        parse_mode="Markdown"
    )
    return REPORTE_FECHA

async def reporte_fecha_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 4: Recibe la fecha y pide la descripción del problema."""
    user_id = update.message.from_user.id
    fecha_compra_str = update.message.text.strip()
    
    try:
        datetime.strptime(fecha_compra_str, '%d/%m/%Y')
    except ValueError:
        await update.message.reply_text(
            "❌ Formato de fecha incorrecto. Usa DD/MM/AAAA (ej: 01/10/2025). Intenta nuevamente:",
            parse_mode="Markdown"
        )
        return REPORTE_FECHA
        
    tmp_reporte[user_id]['fecha_compra'] = fecha_compra_str
    
    await update.message.reply_text(
        "📝 Describe detalladamente el problema que presenta la cuenta. Si tienes una captura de pantalla, ¡puedes enviarla ahora mismo junto con tu texto!",
        parse_mode="Markdown"
    )
    return REPORTE_DESCRIPCION

async def reporte_descripcion_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 5: Recibe la descripción y/o foto y envía el reporte al Admin."""
    user_id = update.message.from_user.id
    data = tmp_reporte.pop(user_id, {})
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

    reporte_msg = (
        "🚨 NUEVO REPORTE DE CUENTA\n"
        "-------------------------------\n"
        f"👤 Usuario ID: {user_id}\n"
        f"📧 Correo reportado: {data.get('correo','')}\n"
        f"🔑 Contraseña reportada: {data.get('pass','')}\n"
        f"📅 Fecha de Compra: {data.get('fecha_compra','')}\n"
        f"🛡 Garantía: {GARANTIA_DIAS} días\n"
        f"🆔 ID de Compra: {data.get('id_compra','')}\n"
        f"📝 Descripción: {descripcion}\n"
        "-------------------------------\n"
        "El administrador debe revisar esta cuenta. La garantía es de 25 días."
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=reporte_msg,
            parse_mode="Markdown"
        )
        if foto_id:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=foto_id,
                caption=f"🆔 ID de Compra: {data.get('id_compra','')}\n📝 Descripción: {descripcion}",
                parse_mode="Markdown"
            )
        await update.message.reply_text(
            "✅ Reporte enviado al administrador. Nos pongamos en contacto contigo pronto.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]])
        )
    except Exception as e:
        logging.error(f"Error al enviar reporte al admin: {e}")
        await update.message.reply_text("❌ Error al enviar el reporte. Por favor, contacta al administrador manualmente.",)

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


async def guardar_material_perfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    data = tmp_venta.get(user_id)
    if not data:
        await update.message.reply_text("❌ No hay registro activo. Usa /addventa de nuevo.")
        return ConversationHandler.END

    correo = data['correo']
    tipo = data['tipo']
    import re
    match = re.search(r'(\d+)', tipo)
    perfiles = int(match.group(1)) if match else 1

    # Guardar el archivo para cada perfil
    for i in range(1, perfiles+1):
        if update.message.document:
            file = await update.message.document.get_file()
            ext = os.path.splitext(update.message.document.file_name)[1]
            filename = f"material_{correo}_perfil{i}{ext}"
            await file.download_to_drive(filename)
        elif update.message.photo:
            file = await update.message.photo[-1].get_file()
            filename = f"material_{correo}_perfil{i}.jpg"
            await file.download_to_drive(filename)

    await update.message.reply_text(f"✅ Material guardado para {perfiles} perfiles. Registro finalizado.")
    tmp_venta.pop(user_id, None)
    return ConversationHandler.END


# --- Flujo para agregar combos (solo Admin) ---

async def addcombo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo el administrador puede agregar combos.")
        return ConversationHandler.END
    context.user_data['nuevo_combo'] = {}
    await update.message.reply_text("📝 Escribe el nombre principal del combo (título):")
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

# Handler para mostrar combos en el menú principal
async def show_combos_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    mensaje = "🎁 *Combos disponibles:*\n\n"
    for i, combo in enumerate(combos):
        mensaje += f"{i+1}. *{combo['titulo']}* ({combo['subnombre']}) - ${combo['precio']:.2f}\n"
        mensaje += "   Plataformas: " + ", ".join(combo['plataformas']) + "\n"
        keyboard.append([InlineKeyboardButton(f"Comprar {combo['titulo']}", callback_data=f"comprar_combo_{i}")])
    keyboard.append([InlineKeyboardButton("⬅️ Volver al menú", callback_data="empezar")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Si viene de CallbackQuery, usar edit_message_text; si no, reply_text
    if getattr(update, "callback_query", None):
        query = update.callback_query
        # Responder al callback para quitar la "carga" en el cliente
        try:
            await query.answer()
        except Exception as e:
            # Ignorar callbacks expirados / inválidos; loguear en debug
            logging.debug(f"Ignored query.answer() error: {e}")
        # Intentar editar el mensaje original; si falla, enviar uno nuevo
        try:
            await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(chat_id=query.from_user.id, text=mensaje, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(mensaje, reply_markup=reply_markup, parse_mode="Markdown")

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

def main():
    """Configuración principal del bot."""
    cargar_clientes()
    application = ApplicationBuilder().token(TOKEN).build()

    # Handler para agregar combos
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
    application.add_handler(CallbackQueryHandler(show_combos_menu, pattern='^show_combos_menu$'))
    application.add_handler(CommandHandler("combos", show_combos_menu))
    application.add_handler(CommandHandler("verclientes", ver_clientes))

    # Comandos generales
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("saldo", saldo))
    application.add_handler(CommandHandler("comandos", comandos))
    application.add_handler(CommandHandler("historial", historial))
    application.add_handler(CommandHandler("cancel", cancel))
    
    # Comandos de administrador
    application.add_handler(CommandHandler("stock", stock_check))
    application.add_handler(CommandHandler("recargar", recargar))
    application.add_handler(CommandHandler("quitarsaldo", quitar_saldo))
    application.add_handler(CommandHandler("consultarsaldo", consultar_saldo))
    application.add_handler(CommandHandler("responder", responder))  # <-- AÑADIR AQUÍ

    # Flujo de agregar venta (Admin)
    addventa_handler = ConversationHandler(
        entry_points=[CommandHandler('addventa', addventa)],
        states={
            AGREGAR_TIPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_tipo)],
            AGREGAR_PERFILES: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_perfiles)],
            AGREGAR_CORREO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_correo)],
            AGREGAR_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_pass)],
            AGREGAR_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_precio)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(addventa_handler)
    
    # Flujo de reporte (Usuario)
    reporte_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(reporte_start, pattern='^iniciar_reporte$')],
        states={
            REPORTE_ID_COMPRA: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_id_compra_recibida)],
            REPORTE_CORREO: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_correo_recibida)],
            REPORTE_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_pass_recibida)],
            REPORTE_FECHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, reporte_fecha_recibida)],
            REPORTE_DESCRIPCION: [MessageHandler(filters.TEXT | filters.PHOTO & ~filters.COMMAND, reporte_descripcion_recibida)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(reporte_handler)

    # Handlers de Compra
    application.add_handler(CallbackQueryHandler(show_categories, pattern='^show_categories$')) 
    application.add_handler(CallbackQueryHandler(show_plataformas, pattern='^category_(completa|perfil)$')) 
    application.add_handler(CallbackQueryHandler(handle_platform_selection, pattern='^select_(completa|perfil)_.*')) 
    application.add_handler(CallbackQueryHandler(handle_compra_final, pattern='^buy_.*')) 
    
    # Handlers de Combos
    application.add_handler(CommandHandler("addcombo", addcombo_start))
    application.add_handler(CommandHandler("combos", show_combos_menu))
    
    # Handlers Varios
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern='^empezar$'))
    application.add_handler(CallbackQueryHandler(show_categories, pattern='^comprar$')) # Redirige el viejo 'comprar' al nuevo menú de categorías
    application.add_handler(CallbackQueryHandler(show_recarga_info, pattern='^mostrar_recarga$', block=False))
    
    # Lógica de borrado de stock
    application.add_handler(CommandHandler("borrarventa", borrar_venta))
    application.add_handler(CallbackQueryHandler(borrar_venta, pattern='^borrar_venta_menu$'))
    application.add_handler(CallbackQueryHandler(mostrar_lista_borrar, pattern='^borrar_(completa|perfil|otro)$'))
    application.add_handler(MessageHandler(filters.TEXT & filters.Chat(ADMIN_ID), borrar_stock_por_indice)) 
    application.add_handler(MessageHandler(filters.PHOTO & filters.Chat(ADMIN_ID), responder_foto))

    # Nuevo handler para la compra de combos
    async def handle_combo_compra(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        inicializar_usuario(user_id)

        try:
            index = int(query.data.split('_')[-1])
        except Exception as e:
            logging.exception(f"handle_combo_compra: índice inválido en callback_data '{query.data}': {e}")
            await query.edit_message_text("❌ Error interno. Intenta de nuevo.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]]))
            return

        if index < 0 or index >= len(combos):
            await query.edit_message_text("❌ Combo no encontrado.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]]))
            return

        combo = combos[index]
        precio = float(combo.get('precio', 0.0))

        if clientes[user_id] < precio:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Saldo insuficiente. Necesitas ${precio:.2f} y solo tienes ${clientes[user_id]:.2f}.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 Recargar saldo", callback_data="mostrar_recarga")], [InlineKeyboardButton("⬅️ Volver al Menú", callback_data="empezar")]]),
                parse_mode="Markdown"
            )
            return

        # Descontar y registrar la compra
        clientes[user_id] -= precio
        guardar_clientes()
        id_compra = str(uuid.uuid4()).split('-')[0].upper()
        log_compra(user_id, combo.get('titulo', 'Combo'), "N/A", "N/A", precio, id_compra)

        logging.info(f"Entrega combo preparada: combo_index={index}, user_id={user_id}, precio={precio:.2f}, saldo_restante={clientes[user_id]:.2f}, id_compra={id_compra}")

        mensaje_entrega = (
            "🎉 ¡Tu combo ha sido entregado! 🎉\n"
            "--------------------------------------\n"
            f"➡️ Combo: {combo.get('titulo','N/A')}\n"
            f"➡️ Descripción: {combo.get('subnombre','')}\n"
            f"➡️ Plataformas: {', '.join(combo.get('plataformas', []))}\n"
            f"➡️ Costo: ${precio:.2f}\n"
            "--------------------------------------\n"
            f"🛡️ Garantía: {GARANTIA_DIAS} días\n"
            f"🆔 ID de Compra: {id_compra}\n"
            "Guarda este ID para cualquier reporte. ¡Disfruta!\n"
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=mensaje_entrega,
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.exception(f"Error enviando mensaje de entrega al usuario {user_id}: {e}")
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ Error enviando entrega de combo a {user_id}: {e}")
            except Exception:
                logging.exception("No se pudo notificar al admin sobre el fallo de entrega de combo.")

        # Envío de material adjunto por plataforma (si existe)
        for plataforma in combo.get('plataformas', []):
            # Nombre de material específico para combos (no usar variables de compra individual)
            material_filename = f"material_{plataforma}_combo.jpg"
            if os.path.exists(material_filename):
                try:
                    with open(material_filename, 'rb') as mf:
                        await context.bot.send_document(
                            chat_id=user_id,
                            document=mf,
                            caption=f"Material para {plataforma} en tu combo"
                        )
                except Exception as e:
                    logging.exception(f"No se pudo enviar material '{material_filename}' a {user_id}: {e}")
                    # continuar con otras plataformas

        await show_main_menu(update, context, welcome_msg="✅ Compra exitosa. ¿Qué deseas hacer ahora?")

    application.add_handler(CallbackQueryHandler(handle_combo_compra, pattern='^comprar_combo_\\d+$'))

    application.run_polling()

if __name__ == '__main__':
    main()
