import logging
import os
import sys
import re
import ast
import time
import subprocess
import requests
import asyncio
import threading
import http.server
import socketserver
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)

# ==============================================================================
# ⚙️ USER CONFIGURATION
# ==============================================================================

# 1. The Token for THIS bot (The Manager)
MANAGER_BOT_TOKEN = "8590724179:AAES-qnrXYLz79vCRphTKgseXN4JYzvcL0U"

# 2. Your Chimkandi API Key (Google AI Studio Key)
CHIMKANDI_API_KEY = "AIzaSyCE1ZG6R3yMF-95UNO0dlEjBFI4GtEOXOc"

# File name for the bot we will generate
GENERATED_BOT_FILE = "generated_bot.py"

# ==============================================================================
# 🌐 RENDER KEEP-ALIVE SERVER
# ==============================================================================
# This satisfies Render's port requirement and allows Uptime Bots to ping your URL:
# https://host-lgnm.onrender.com

class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Chimkandi Manager is Alive and Running! 24/7")

def start_keep_alive():
    # Render sets the PORT environment variable. Default to 8080 if local.
    PORT = int(os.environ.get("PORT", 8080))
    # Prevent address in use errors
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("", PORT), HealthCheckHandler) as httpd:
            print(f"🌍 Chimkandi Keep-Alive Server running on port {PORT}")
            httpd.serve_forever()
    except Exception as e:
        print(f"⚠️ Keep-alive server warning: {e}")

# ==============================================================================
# SETUP & LOGGING
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation States
SELECTING_ACTION, WAITING_FOR_TOKEN, WAITING_FOR_PROMPT, WAITING_FOR_MANUAL_CODE = range(4)

# Global process holder
hosted_process = None

# ==============================================================================
# 🧠 CHIMKANDI AI & CODE LOGIC
# ==============================================================================
def call_chimkandi_flash(user_prompt):
    """Calls Chimkandi AI to write the Python code."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={CHIMKANDI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    system_instruction = (
        "You are Chimkandi AI, an expert Python Telegram Bot developer. "
        "1. Write a COMPLETE, runnable python script using 'python-telegram-bot' library (v20+). "
        "2. IMPORTANT: The bot must read its token from os.getenv('BOT_TOKEN'). "
        "3. Do NOT hardcode tokens. "
        "4. Output ONLY the python code inside markdown code blocks ```python ... ```. "
        "5. Keep it simple and robust."
    )
    
    full_prompt = f"{system_instruction}\n\nUser Request: {user_prompt}"
    data = {"contents": [{"parts": [{"text": full_prompt}]}]}

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        return result['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        logger.error(f"Chimkandi API Error: {e}")
        return None

def extract_and_clean_code(text):
    """Cleans the AI code and auto-fixes token errors."""
    match = re.search(r"```python(.*?)```", text, re.DOTALL)
    if not match:
        match = re.search(r"```(.*?)```", text, re.DOTALL)
    
    code = match.group(1).strip() if match else text.strip()

    if "import os" not in code:
        code = "import os\n" + code

    code = re.sub(
        r"\.token\(['\"].*?['\"]\)", 
        ".token(os.getenv('BOT_TOKEN'))", 
        code
    )
    
    return code

# ==============================================================================
# 🎮 BOT HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main Menu"""
    keyboard = [
        [InlineKeyboardButton("✨ Chimkandi Creator (AI)", callback_data='start_ai')],
        [InlineKeyboardButton("📂 Manual Deploy (Upload/Paste)", callback_data='start_manual')],
        [InlineKeyboardButton("🛑 Stop Running Bot", callback_data='stop_host')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Welcome to Chimkandi AI Bot Factory\n\n"
        "Choose an option:",
        reply_markup=reply_markup
    )
    return SELECTING_ACTION

async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data

    if choice == 'start_ai':
        context.user_data['mode'] = 'ai'
        await query.edit_message_text(
            "🔑 Step 1: Enter Token\n\n"
            "Paste the Bot Token for the NEW bot."
        )
        return WAITING_FOR_TOKEN

    elif choice == 'start_manual':
        context.user_data['mode'] = 'manual'
        await query.edit_message_text(
            "🔑 Step 1: Enter Token\n\n"
            "Paste the Bot Token for the NEW bot you are uploading manually."
        )
        return WAITING_FOR_TOKEN

    elif choice == 'stop_host':
        global hosted_process
        if hosted_process:
            hosted_process.terminate()
            hosted_process = None
            await query.edit_message_text(
                "🔴 Bot Stopped.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='back_to_menu')]])
            )
        else:
            await query.edit_message_text(
                "ℹ️ No bot is currently running.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='back_to_menu')]])
            )
        return ConversationHandler.END
    
    elif choice == 'back_to_menu':
        return await start(update, context)

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Save the token and route based on mode."""
    token = update.message.text.strip()
    context.user_data['target_bot_token'] = token
    mode = context.user_data.get('mode')

    if mode == 'ai':
        await update.message.reply_text(
            "✅ Token Received.\n\n"
            "📝 Step 2: Describe your Bot\n"
            "Tell Chimkandi what this bot should do."
        )
        return WAITING_FOR_PROMPT
    
    elif mode == 'manual':
        await update.message.reply_text(
            "✅ Token Received.\n\n"
            "📂 Step 2: Upload Script\n"
            "Please upload your `.py` file OR paste the Python code here."
        )
        return WAITING_FOR_MANUAL_CODE

async def receive_manual_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2 (Manual): Receive file/text and deploy."""
    msg = await update.message.reply_text("📥 Processing your script...")
    
    code = ""
    
    # Check if it's a file or text
    if update.message.document:
        file_obj = await update.message.document.get_file()
        await file_obj.download_to_drive(GENERATED_BOT_FILE)
        # Read the file to ensure we can clean it/inject token logic
        with open(GENERATED_BOT_FILE, 'r', encoding='utf-8') as f:
            code = f.read()
    elif update.message.text:
        code = update.message.text
    else:
        await msg.edit_text("❌ Please send a valid Python file or text code.")
        return WAITING_FOR_MANUAL_CODE

    # Clean and Ensure Token Logic is present
    final_code = extract_and_clean_code(code)
    
    # Syntax Check
    try:
        ast.parse(final_code)
    except SyntaxError as e:
        await msg.edit_text(f"⚠️ Syntax Error in your code:\n{e}")
        return ConversationHandler.END

    # Save final version
    with open(GENERATED_BOT_FILE, "w", encoding="utf-8") as f:
        f.write(final_code)
    
    await msg.edit_text("✅ Script Verified. Deploying...")
    
    # Trigger Deployment
    return await deploy_process(update, context, msg)

async def generate_and_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2 (AI): Generate code and deploy."""
    user_prompt = update.message.text
    msg = await update.message.reply_text("🤖 Chimkandi is Coding...")

    loop = asyncio.get_running_loop()
    raw_code = await loop.run_in_executor(None, call_chimkandi_flash, user_prompt)
    
    if not raw_code:
        await msg.edit_text("❌ API Error. Check your API Key.")
        return ConversationHandler.END

    final_code = extract_and_clean_code(raw_code)
    
    try:
        ast.parse(final_code)
    except SyntaxError as e:
        await msg.edit_text(f"⚠️ Chimkandi Error:\n{e}")
        return ConversationHandler.END

    with open(GENERATED_BOT_FILE, "w", encoding="utf-8") as f:
        f.write(final_code)

    await msg.edit_text("✅ Code Written. Implementing...")
    
    # Trigger Deployment
    return await deploy_process(update, context, msg)

async def deploy_process(update, context, status_message):
    """Shared Deployment Logic."""
    global hosted_process
    if hosted_process:
        hosted_process.terminate()

    target_token = context.user_data.get('target_bot_token')
    if not target_token:
        await status_message.edit_text("❌ Error: Token lost. Restart.")
        return ConversationHandler.END

    await status_message.edit_text("🔄 Initializing Server...")
    time.sleep(0.5)
    await status_message.edit_text("📦 Checking Dependencies...")
    time.sleep(0.5)
    await status_message.edit_text("🚀 Launching Bot...")

    env = os.environ.copy()
    env["BOT_TOKEN"] = target_token

    try:
        hosted_process = subprocess.Popen(
            [sys.executable, GENERATED_BOT_FILE],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        await status_message.edit_text(
            "🟢 Bot Deployed Successfully!\n\n"
            "Your bot is LIVE 24/7.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='back_to_menu')]])
        )
        
        # Send the file back to user
        await update.message.reply_document(document=open(GENERATED_BOT_FILE, 'rb'))

    except Exception as e:
        await status_message.edit_text(f"❌ Implementation Error: {e}")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Action Cancelled.")
    return ConversationHandler.END

# ==============================================================================
# MAIN RUNNER
# ==============================================================================
if __name__ == '__main__':
    if "PASTE_YOUR" in MANAGER_BOT_TOKEN:
        print("❌ ERROR: You forgot to paste your Bot Token in the script!")
        sys.exit()
    
    # 1. Start Keep-Alive Server
    server_thread = threading.Thread(target=start_keep_alive, daemon=True)
    server_thread.start()

    # 2. Start Bot
    app = ApplicationBuilder().token(MANAGER_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CallbackQueryHandler(menu_button, pattern='^back_to_menu$')
        ],
        states={
            SELECTING_ACTION: [CallbackQueryHandler(menu_button)],
            WAITING_FOR_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            WAITING_FOR_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, generate_and_deploy)],
            WAITING_FOR_MANUAL_CODE: [MessageHandler(filters.TEXT | filters.Document.ALL, receive_manual_code)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv)
    print("✅ Chimkandi Manager is Running...")
    app.run_polling()
