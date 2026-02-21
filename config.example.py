"""
Configuration for anti-bot — Telegram ↔ Antigravity IDE Bridge.

Copy this file to config.py and fill in your values:
    cp config.example.py config.py
"""

import os

# ── Telegram ─────────────────────────────────────────────────────────────────
# Get this from @BotFather on Telegram
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# Your personal Telegram chat ID (security: only this ID can send commands)
ALLOWED_CHAT_ID = "YOUR_CHAT_ID_HERE"

# ── Gemini API ───────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")
GEMINI_MODEL = "gemini-2.0-flash"

# System prompt — tells Gemini how to behave
SYSTEM_PROMPT = (
    "You are anti-bot, an AI coding assistant accessible via Telegram. "
    "You help with coding tasks, debugging, architecture, DevOps, scripting, "
    "and general technical questions. Keep responses concise since they appear "
    "in a mobile chat. Use markdown formatting. When showing code, use "
    "fenced code blocks. The user may continue this conversation later in "
    "the Antigravity IDE on their MacBook, so be thorough in your reasoning."
)

# ── Session Logging ──────────────────────────────────────────────────────────
# Conversations are saved here so Antigravity IDE can pick them up
SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")

# ── Shell Execution (when Mac is on) ─────────────────────────────────────────
BLOCKED_COMMANDS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "halt",
    ':(){:|:&};:',  # fork bomb
]

MAX_OUTPUT_LENGTH = 3800
COMMAND_TIMEOUT = 60
SCREENSHOT_PATH = "/tmp/anti-bot_screenshot.png"
