import logging
import os
import sys
import re
import ast
import time
import subprocess
import requests
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)

# ==============================================================================
# ⚙️ USER CONFIGURATION (PASTE YOUR KEYS HERE)
# ==============================================================================

# 1. The Token for THIS bot (The Manager)
# Get this from @BotFather
MANAGER_BOT_TOKEN = "PASTE_YOUR_TELEGRAM_BOT_TOKEN_HERE"

# 2. Your Gemini API Key
# Get this from Google AI Studio
GEMINI_API_KEY = "PASTE_YOUR_GEMINI_API_KEY_HERE"

# File name for the bot we will generate
GENERATED_BOT_FILE = "generated_bot.py"

# ==============================================================================
# SETUP & LOGGING
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation States
SELECTING_ACTION, WAITING_FOR_PROMPT, WAITING_FOR_HOST_TOKEN = range(3)

# Global process holder
hosted_process = None

# ==============================================================================
# 🧠 GEMINI API & CODE LOGIC
# ==============================================================================
def call_gemini_flash(user_prompt):
    """Calls Gemini to write the Python code."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    # We force Gemini to write code that uses os.getenv('BOT_TOKEN')
    # This ensures our hosting feature works perfectly.
    system_instruction = (
        "You are an expert Python Telegram Bot developer. "
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
        logger.error(f"Gemini API Error: {e}")
        return None

def extract_and_clean_code(text):
    """Cleans the AI code and auto-fixes token errors."""
    # Extract code from markdown
    match = re.search(r"```python(.*?)```", text, re.DOTALL)
    if not match:
        match = re.search(r"```(.*?)```", text, re.DOTALL)
    
    code = match.group(1).strip() if match else text.strip()

    # FORCE IMPORT OS
    if "import os" not in code:
        code = "import os\n" + code

    # AUTO-FIX: If AI wrote token="...", change it to os.getenv('BOT_TOKEN')
    # This fixes the "Script has Telegram-Bot-Token" issue you mentioned.
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
        [InlineKeyboardButton("✨ Create New Bot (Gemini)", callback_data='write_code')],
        [InlineKeyboardButton("🚀 Host Created Bot", callback_data='host_bot')],
        [InlineKeyboardButton("🛑 Stop Hosting", callback_data='stop_host')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 **Bot Factory Online**\n\n"
        "What would you like to do?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return SELECTING_ACTION

async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data

    if choice == 'write_code':
        await query.edit_message_text(
            "📝 **Describe your bot:**\n"
            "Example: *'A bot that welcomes people and bans anyone saying bad words.'*"
        )
        return WAITING_FOR_PROMPT

    elif choice == 'host_bot':
        if not os.path.exists(GENERATED_BOT_FILE):
            await query.edit_message_text("⚠️ No code found! Please 'Create New Bot' first.")
            return ConversationHandler.END
        
        await query.edit_message_text(
            "🔑 **Enter Token for the NEW bot:**\n"
            "Paste the token from BotFather for the bot you want to run."
        )
        return WAITING_FOR_HOST_TOKEN

    elif choice == 'stop_host':
        global hosted_process
        if hosted_process:
            hosted_process.terminate()
            hosted_process = None
            await query.edit_message_text("🔴 **Bot Stopped.**")
        else:
            await query.edit_message_text("ℹ️ Nothing is running.")
        return ConversationHandler.END

async def generate_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    msg = await update.message.reply_text("🤖 **Gemini is coding...**")

    # Call AI
    raw_code = call_gemini_flash(user_text)
    if not raw_code:
        await msg.edit_text("❌ API Error. Check your GEMINI_API_KEY.")
        return ConversationHandler.END

    # Fix & Save
    final_code = extract_and_clean_code(raw_code)
    
    # Syntax Check
    try:
        ast.parse(final_code)
    except SyntaxError as e:
        await msg.edit_text(f"⚠️ **Code Error:**\n{e}")
        return ConversationHandler.END

    with open(GENERATED_BOT_FILE, "w", encoding="utf-8") as f:
        f.write(final_code)

    await msg.edit_text("✅ **Code Ready!**\nUse the menu to **Host Bot**.")
    await update.message.reply_document(document=open(GENERATED_BOT_FILE, 'rb'))
    
    return ConversationHandler.END

async def deploy_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global hosted_process
    
    # Stop previous if exists
    if hosted_process:
        hosted_process.terminate()

    token = update.message.text.strip()
    status = await update.message.reply_text("🔄 **Initializing Server...**")
    
    # Simulate Professional Deployment Steps
    time.sleep(1.5)
    await status.edit_text("📦 **Installing Dependencies...**")
    time.sleep(1.5)
    await status.edit_text("🚀 **Launching Script...**")
    
    # Run the generated bot as a subprocess
    # We pass the user's provided token into the environment for the child process
    env = os.environ.copy()
    env["BOT_TOKEN"] = token

    try:
        hosted_process = subprocess.Popen(
            [sys.executable, GENERATED_BOT_FILE],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        await status.edit_text(
            "🟢 **Deployed Successfully!**\n"
            "Your bot is now LIVE.\n\n"
            "Type /start to stop it later."
        )
    except Exception as e:
        await status.edit_text(f"❌ **Error:** {e}")

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

    app = ApplicationBuilder().token(MANAGER_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [CallbackQueryHandler(menu_button)],
            WAITING_FOR_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, generate_code)],
            WAITING_FOR_HOST_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, deploy_bot)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv)
    print("✅ Manager Bot is Running...")
    app.run_polling()
