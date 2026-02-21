#!/usr/bin/env python3
"""
anti-bot — Telegram ↔ Antigravity IDE Bridge
Chat with Gemini from your phone, pick up the conversation in the IDE.
"""

import asyncio
import html
import logging
import os
import subprocess
import sys
from datetime import datetime

from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_CHAT_ID,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    SYSTEM_PROMPT,
    SESSIONS_DIR,
    BLOCKED_COMMANDS,
    MAX_OUTPUT_LENGTH,
    COMMAND_TIMEOUT,
    SCREENSHOT_PATH,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Gemini Setup ─────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)

# Chat config with system instruction
chat_config = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
)

# ── Session State ────────────────────────────────────────────────────────────
# Persistent chat object (manages history automatically)
active_chat = None
# Mirror of history for session saving
conversation_history: list[dict] = []
session_start_time: str = ""


def _ensure_sessions_dir():
    """Create the sessions directory if it doesn't exist."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _start_new_session():
    """Start a fresh conversation session."""
    global active_chat, conversation_history, session_start_time
    conversation_history = []
    session_start_time = datetime.now().strftime("%Y-%m-%d_%H%M")
    # Create a new chat session with the Gemini model
    active_chat = client.chats.create(
        model=GEMINI_MODEL,
        config=chat_config,
    )
    logger.info(f"New session started: {session_start_time}")


def _save_session():
    """Save the current conversation to a markdown file."""
    if not conversation_history:
        return

    _ensure_sessions_dir()
    timestamp = session_start_time or datetime.now().strftime("%Y-%m-%d_%H%M")

    # Build markdown content
    lines = [
        f"# Telegram Session — {timestamp}",
        "",
        "> This conversation was held via Telegram (anti-bot).",
        "> To continue in Antigravity IDE, reference this file.",
        "",
        "---",
        "",
    ]

    for msg in conversation_history:
        role = msg["role"]
        text = msg["text"]

        if role == "user":
            lines.append("## 👤 You")
            lines.append("")
            lines.append(text)
            lines.append("")
        else:
            lines.append("## 🤖 anti-bot")
            lines.append("")
            lines.append(text)
            lines.append("")

    content = "\n".join(lines)

    # Save as timestamped file
    session_file = os.path.join(SESSIONS_DIR, f"session_{timestamp}.md")
    with open(session_file, "w") as f:
        f.write(content)

    # Also save as latest.md (always overwritten)
    latest_file = os.path.join(SESSIONS_DIR, "latest.md")
    with open(latest_file, "w") as f:
        f.write(content)

    logger.info(f"Session saved to {session_file}")


# ── Security ─────────────────────────────────────────────────────────────────
def is_authorized(update: Update) -> bool:
    """Only allow messages from the configured chat ID."""
    chat_id = str(update.effective_chat.id)
    if chat_id != str(ALLOWED_CHAT_ID):
        logger.warning(f"Unauthorized access attempt from chat_id={chat_id}")
        return False
    return True


def is_blocked(command: str) -> bool:
    """Check if a command matches the blocklist."""
    cmd_lower = command.strip().lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked.lower() in cmd_lower:
            return True
    return False


# ── Gemini Chat ──────────────────────────────────────────────────────────────
async def _ask_gemini(prompt: str) -> str:
    """Send a prompt to Gemini with conversation history and return the response."""
    global active_chat

    # Start session if needed
    if not session_start_time or active_chat is None:
        _start_new_session()

    # Track user message in our history mirror
    conversation_history.append({"role": "user", "text": prompt})

    try:
        # Send message — the chat object manages history automatically
        response = active_chat.send_message(prompt)
        reply = response.text

        # Track model response in our history mirror
        conversation_history.append({"role": "model", "text": reply})

        # Auto-save after each exchange
        _save_session()

        return reply

    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        # Remove the failed user message from history
        conversation_history.pop()
        return f"❌ Gemini API error: `{e}`"


# ── Command Handlers ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — intro message."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "🤖 *anti-bot — Antigravity Bridge*\n\n"
        "💬 *AI Chat (works anytime):*\n"
        "  Just type anything → Gemini responds\n"
        "  `/ask <prompt>` — same thing, explicit\n"
        "  `/clear` — start a fresh session\n"
        "  `/history` — show session so far\n\n"
        "🖥 *Mac Control (when Mac is on):*\n"
        "  `/run <command>` — execute shell command\n"
        "  `/file <path>` — download a file\n"
        "  `/screen` — take a screenshot\n"
        "  `/status` — check Mac health\n\n"
        "📝 Conversations are saved so you can\n"
        "pick them up in Antigravity IDE later.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await cmd_start(update, context)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear — start a fresh session."""
    if not is_authorized(update):
        return
    _start_new_session()
    await update.message.reply_text(
        "🗑 Session cleared. Starting fresh.\n"
        "Previous sessions are still saved in the `sessions/` folder."
    )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /history — show the current session summary."""
    if not is_authorized(update):
        return

    if not conversation_history:
        await update.message.reply_text("📭 No conversation yet. Just type something!")
        return

    count = len([m for m in conversation_history if m["role"] == "user"])
    await update.message.reply_text(
        f"📝 *Current session:* {session_start_time}\n"
        f"💬 {count} messages exchanged\n\n"
        f"Session is auto-saved to:\n"
        f"`sessions/session_{session_start_time}.md`",
        parse_mode="Markdown",
    )


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask <prompt> — explicit Gemini query."""
    if not is_authorized(update):
        return

    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text(
            "⚠️ Usage: `/ask <your question>`", parse_mode="Markdown"
        )
        return

    await _handle_ai_message(update, prompt)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text — treat as AI conversation."""
    if not is_authorized(update):
        return

    prompt = update.message.text.strip()
    if not prompt:
        return

    await _handle_ai_message(update, prompt)


async def _handle_ai_message(update: Update, prompt: str) -> None:
    """Send prompt to Gemini and reply with the response."""
    # Show typing indicator
    await update.message.reply_text("🧠 Thinking...")

    reply = await _ask_gemini(prompt)

    # Telegram message limit is 4096 chars — split if needed
    if len(reply) <= 4096:
        try:
            await update.message.reply_text(reply, parse_mode="Markdown")
        except Exception:
            # Fallback if markdown parsing fails
            await update.message.reply_text(reply)
    else:
        # Send in chunks
        for i in range(0, len(reply), 4096):
            chunk = reply[i : i + 4096]
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(chunk)


# ── Mac Control Commands ─────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — quick health check."""
    if not is_authorized(update):
        return

    try:
        uptime = subprocess.run(
            ["uptime"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        battery = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        hostname = subprocess.run(
            ["hostname"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception as e:
        uptime = battery = hostname = f"(error: {e})"

    session_info = (
        f"📝 Active session: `{session_start_time}`"
        if session_start_time
        else "📝 No active session"
    )

    await update.message.reply_text(
        f"✅ *Online*\n\n"
        f"🖥 `{hostname}`\n"
        f"⏱ {uptime}\n"
        f"🔋 {battery}\n\n"
        f"{session_info}",
        parse_mode="Markdown",
    )


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /run <command> — execute a shell command."""
    if not is_authorized(update):
        return

    command = " ".join(context.args) if context.args else ""
    if not command:
        await update.message.reply_text(
            "⚠️ Usage: `/run <command>`", parse_mode="Markdown"
        )
        return

    await _execute_and_reply(update, command)


async def cmd_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /file <path> — send a file to Telegram."""
    if not is_authorized(update):
        return

    path = " ".join(context.args) if context.args else ""
    if not path:
        await update.message.reply_text(
            "⚠️ Usage: `/file <path>`", parse_mode="Markdown"
        )
        return

    path = os.path.expanduser(os.path.expandvars(path))

    if not os.path.exists(path):
        await update.message.reply_text(
            f"❌ File not found: `{path}`", parse_mode="Markdown"
        )
        return

    if os.path.isdir(path):
        await update.message.reply_text(
            "❌ That's a directory, not a file.", parse_mode="Markdown"
        )
        return

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > 50:
        await update.message.reply_text(
            f"❌ File is too large ({size_mb:.1f} MB). Telegram limit is 50 MB.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_document(
        document=open(path, "rb"),
        filename=os.path.basename(path),
        caption=f"📁 `{path}`",
        parse_mode="Markdown",
    )


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /screen — take a screenshot and send it."""
    if not is_authorized(update):
        return

    await update.message.reply_text("📸 Taking screenshot...")

    try:
        subprocess.run(
            ["screencapture", "-x", SCREENSHOT_PATH],
            timeout=10,
            check=True,
        )
        if os.path.exists(SCREENSHOT_PATH):
            await update.message.reply_photo(
                photo=open(SCREENSHOT_PATH, "rb"),
                caption="🖥 Screenshot",
            )
            os.remove(SCREENSHOT_PATH)
        else:
            await update.message.reply_text("❌ Screenshot failed — file not created.")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("❌ Screenshot timed out.")
    except Exception as e:
        await update.message.reply_text(
            f"❌ Screenshot error: `{e}`", parse_mode="Markdown"
        )


# ── Shell Execution Helper ───────────────────────────────────────────────────
async def _execute_and_reply(update: Update, command: str) -> None:
    """Execute a command and send the output back."""
    if is_blocked(command):
        await update.message.reply_text(
            f"🚫 *Blocked* — this command is on the safety blocklist:\n`{command}`",
            parse_mode="Markdown",
        )
        return

    logger.info(f"Executing: {command}")
    await update.message.reply_text(
        f"⏳ Running: `{html.escape(command)}`", parse_mode="HTML"
    )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            cwd=os.path.expanduser("~"),
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        exit_code = result.returncode

        parts = []
        if exit_code == 0:
            parts.append("✅ *Success*")
        else:
            parts.append(f"⚠️ *Exit code: {exit_code}*")

        if stdout:
            if len(stdout) > MAX_OUTPUT_LENGTH:
                stdout = stdout[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"
            parts.append(f"```\n{stdout}\n```")

        if stderr:
            if len(stderr) > MAX_OUTPUT_LENGTH:
                stderr = stderr[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"
            parts.append(f"*stderr:*\n```\n{stderr}\n```")

        if not stdout and not stderr:
            parts.append("_(no output)_")

        response = "\n\n".join(parts)

        if len(response) <= 4096:
            await update.message.reply_text(response, parse_mode="Markdown")
        else:
            for i in range(0, len(response), 4096):
                chunk = response[i : i + 4096]
                await update.message.reply_text(chunk, parse_mode="Markdown")

    except subprocess.TimeoutExpired:
        await update.message.reply_text(
            f"⏰ *Timed out* after {COMMAND_TIMEOUT}s:\n`{command}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ *Error:*\n`{e}`",
            parse_mode="Markdown",
        )


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ ERROR: Set TELEGRAM_BOT_TOKEN in config.py first!")
        sys.exit(1)

    if not ALLOWED_CHAT_ID:
        print("❌ ERROR: Set ALLOWED_CHAT_ID in config.py first!")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("❌ ERROR: Set GEMINI_API_KEY in config.py or env!")
        sys.exit(1)

    # Ensure sessions directory exists
    _ensure_sessions_dir()

    # Start a fresh session
    _start_new_session()

    print("🤖 anti-bot starting (Antigravity Bridge)...")
    print(f"   Authorized chat ID: {ALLOWED_CHAT_ID}")
    print(f"   Gemini model: {GEMINI_MODEL}")
    print(f"   Sessions dir: {SESSIONS_DIR}")
    print("   Waiting for commands...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers — AI chat
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("history", cmd_history))

    # Register handlers — Mac control
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("file", cmd_file))
    app.add_handler(CommandHandler("screen", cmd_screen))

    # Plain text → AI chat
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Start polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
