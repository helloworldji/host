import os
import sys
import subprocess
import threading
import logging
import ast
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    MessageHandler, filters, ConversationHandler
)

# --- CONFIGURATION ---
# 1. Get this token from @BotFather. 
# 2. Add it to Render Environment Variables as: TELEGRAM_HOST_TOKEN
HOST_BOT_TOKEN = os.environ.get("TELEGRAM_HOST_TOKEN") 

# Folder to store user bots
BOTS_FOLDER = "user_bots"

# States for the conversation
ASK_TOKEN, ASK_FILE = range(2)

# Store running processes: {user_id: subprocess.Popen}
running_bots = {}

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- WEB SERVER (For UptimeRobot) ---
# This keeps the Render service alive 24/7
app = Flask(__name__)

@app.route('/')
def home():
    count = len(running_bots)
    return f"Telegram Host Bot is alive! Currently hosting {count} bots."

def run_flask():
    # Render assigns a random port in the PORT env var
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- MAGIC DEPENDENCY INSTALLER ---
def install_imports(file_path):
    """
    Scans the uploaded file for imports and installs them via pip.
    Handles special cases like 'telegram' -> 'python-telegram-bot'.
    """
    print(f"[INSTALLER] Scanning {file_path} for libraries...")
    try:
        with open(file_path, "r") as f:
            tree = ast.parse(f.read())
        
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.add(n.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0])
        
        # Filter standard libraries (incomplete list but covers basics)
        sys_modules = sys.builtin_module_names
        common_std_libs = [
            'os', 'sys', 'time', 'math', 'random', 'datetime', 'asyncio', 
            'json', 'logging', 'subprocess', 'threading', 'typing'
        ]
        
        for lib in imports:
            if lib in common_std_libs or lib in sys_modules:
                continue
            
            # --- SPECIAL MAPPINGS ---
            # Users import 'telegram', but pip needs 'python-telegram-bot'
            if lib == 'telegram':
                package_name = 'python-telegram-bot'
            elif lib == 'PIL':
                package_name = 'Pillow'
            else:
                package_name = lib

            print(f"[INSTALLER] Installing: {package_name} (for import '{lib}')")
            # Install quietly
            subprocess.call([sys.executable, "-m", "pip", "install", package_name])
            
    except Exception as e:
        print(f"[INSTALLER ERROR] Failed to analyze/install imports: {e}")

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the hosting process."""
    await update.message.reply_text(
        "🤖 **Telegram Bot Host**\n\n"
        "I can host a Telegram bot for you.\n"
        "Just follow these steps:\n\n"
        "1. Send me your **Bot Token** (from @BotFather).\n"
        "2. Send me your **.py script**.\n\n"
        "Send /cancel to stop.\n\n"
        "👇 **Please paste your Bot Token now:**",
        parse_mode="Markdown"
    )
    return ASK_TOKEN

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stores the token temporarily."""
    token = update.message.text.strip()
    
    # Basic validation (Telegram tokens usually have a colon)
    if ':' not in token:
        await update.message.reply_text("❌ Invalid token format. Please check and send again.")
        return ASK_TOKEN
    
    context.user_data['new_bot_token'] = token
    await update.message.reply_text("✅ Token accepted! Now **upload your Python file (.py)**.")
    return ASK_FILE

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Downloads file, installs deps, and runs the bot."""
    user_id = update.effective_user.id
    document = update.message.document
    
    if not document or not document.file_name.endswith('.py'):
        await update.message.reply_text("❌ Please upload a valid `.py` file.")
        return ASK_FILE

    # Create storage folder
    if not os.path.exists(BOTS_FOLDER):
        os.makedirs(BOTS_FOLDER)

    # Save file as bot_{user_id}.py so one user doesn't overwrite another
    filename = f"bot_{user_id}.py"
    file_path = os.path.join(BOTS_FOLDER, filename)
    
    new_file = await document.get_file()
    await new_file.download_to_drive(file_path)
    
    await update.message.reply_text("⏳ File received. Installing dependencies... (This might take a moment)")

    # 1. Kill existing bot if user already has one running
    if user_id in running_bots:
        old_process = running_bots[user_id]
        old_process.terminate()
        try:
            old_process.wait(timeout=5)
        except:
            old_process.kill()
        del running_bots[user_id]
        await update.message.reply_text("🔄 Restarting your bot instance...")

    # 2. Install Dependencies (Magic Install)
    # We do this in a thread or simple blocking call (Render has fast internet)
    try:
        install_imports(file_path)
    except Exception as e:
        logger.error(f"Dependency install failed: {e}")

    # 3. Launch the new Bot
    # We inject the TOKEN into the environment variables
    bot_env = os.environ.copy()
    user_token = context.user_data['new_bot_token']
    
    # We set typical env var names users might use
    bot_env["BOT_TOKEN"] = user_token
    bot_env["TELEGRAM_TOKEN"] = user_token
    bot_env["TOKEN"] = user_token

    try:
        # Run python -u (unbuffered) so logs appear in Render console
        process = subprocess.Popen([sys.executable, '-u', file_path], env=bot_env)
        running_bots[user_id] = process
        
        await update.message.reply_text(
            f"🚀 **Bot Deployed Successfully!**\n\n"
            f"If your script uses `os.getenv('BOT_TOKEN')`, it will work immediately.\n"
            f"Send /stopbot to shut it down."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to launch bot: {e}")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Hosting cancelled.")
    return ConversationHandler.END

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in running_bots:
        running_bots[user_id].terminate()
        del running_bots[user_id]
        await update.message.reply_text("🛑 Your bot has been stopped.")
    else:
        await update.message.reply_text("❓ You don't have a bot running.")

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # 1. Start Flask (for Uptime) in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Check for the Host's own token
    if not HOST_BOT_TOKEN:
        print("CRITICAL ERROR: 'TELEGRAM_HOST_TOKEN' environment variable is missing.")
        sys.exit(1)

    # 3. Setup Telegram Bot
    application = ApplicationBuilder().token(HOST_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            ASK_FILE: [MessageHandler(filters.Document.FileExtension("py"), receive_file)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('stopbot', stop_bot))

    print("[HOST] Telegram Host Bot is listening...")
    application.run_polling()
