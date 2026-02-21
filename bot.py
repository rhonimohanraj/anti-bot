#!/usr/bin/env python3
"""
anti-bot — Telegram ↔ Antigravity IDE Bridge
An agentic coding assistant: chat, edit files, run tasks from Telegram.
Pick up the work seamlessly in the Antigravity IDE.
"""

import asyncio
import difflib
import html
import json
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
    PROJECT_DIR,
    MAX_FILE_SIZE,
    REQUIRE_APPROVAL,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Gemini Setup ─────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)

chat_config = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
)

# ── Session State ────────────────────────────────────────────────────────────
active_chat = None
conversation_history: list[dict] = []
action_log: list[dict] = []  # Rich action log for continuity
session_start_time: str = ""
current_project_dir: str = PROJECT_DIR

# Pending approval state
pending_action: dict = None  # Stores action waiting for ✅/❌


def _ensure_sessions_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _start_new_session():
    global active_chat, conversation_history, action_log, session_start_time
    conversation_history = []
    action_log = []
    session_start_time = datetime.now().strftime("%Y-%m-%d_%H%M")
    active_chat = client.chats.create(
        model=GEMINI_MODEL,
        config=chat_config,
    )
    logger.info(f"New session started: {session_start_time}")


def _log_action(action_type: str, details: dict):
    """Log an action for continuity with the IDE."""
    action_log.append({
        "type": action_type,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **details,
    })
    _save_session()


def _resolve_path(path: str) -> str:
    """Resolve a path relative to the current project directory."""
    path = os.path.expanduser(os.path.expandvars(path.strip()))
    if not os.path.isabs(path):
        path = os.path.join(current_project_dir, path)
    return os.path.normpath(path)


# ── Session Saving (Rich Action Log) ────────────────────────────────────────
def _save_session():
    if not conversation_history and not action_log:
        return

    _ensure_sessions_dir()
    timestamp = session_start_time or datetime.now().strftime("%Y-%m-%d_%H%M")

    lines = [
        f"# Telegram Session — {timestamp}",
        "",
        "> anti-bot action log. To continue in Antigravity IDE, reference this file.",
        f"> Project directory: `{current_project_dir}`",
        "",
        "---",
        "",
    ]

    for msg in conversation_history:
        role = msg["role"]
        text = msg["text"]
        action = msg.get("action")

        if role == "user":
            lines.append("## 👤 You")
            lines.append("")
            lines.append(text)
            lines.append("")
        elif role == "model":
            lines.append("## 🤖 anti-bot")
            lines.append("")
            lines.append(text)
            lines.append("")

        if action:
            atype = action["type"]
            icon = {"FILE_VIEW": "📂", "FILE_EDIT": "✏️", "FILE_CREATE": "🆕",
                    "DIR_LIST": "📁", "COMMAND_RUN": "⚡", "TASK_EXECUTE": "🚀",
                    "PROJECT_SET": "📍"}.get(atype, "📌")

            lines.append(f"## {icon} ACTION: {atype}")
            for k, v in action.items():
                if k in ("type", "timestamp"):
                    continue
                if k == "diff":
                    lines.append(f"**Diff:**")
                    lines.append("```diff")
                    lines.append(str(v))
                    lines.append("```")
                elif k == "output":
                    lines.append(f"**Output:**")
                    lines.append("```")
                    lines.append(str(v)[:2000])
                    lines.append("```")
                elif k == "content_preview":
                    lines.append(f"**Preview:**")
                    lines.append("```")
                    lines.append(str(v)[:2000])
                    lines.append("```")
                else:
                    lines.append(f"**{k.replace('_', ' ').title()}:** `{v}`")
            lines.append("")

    content = "\n".join(lines)

    session_file = os.path.join(SESSIONS_DIR, f"session_{timestamp}.md")
    with open(session_file, "w") as f:
        f.write(content)

    latest_file = os.path.join(SESSIONS_DIR, "latest.md")
    with open(latest_file, "w") as f:
        f.write(content)

    logger.info(f"Session saved to {session_file}")


# ── Security ─────────────────────────────────────────────────────────────────
def is_authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id)
    if chat_id != str(ALLOWED_CHAT_ID):
        logger.warning(f"Unauthorized access attempt from chat_id={chat_id}")
        return False
    return True


def is_blocked(command: str) -> bool:
    cmd_lower = command.strip().lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked.lower() in cmd_lower:
            return True
    return False


# ── Gemini Chat ──────────────────────────────────────────────────────────────
async def _ask_gemini(prompt: str, action: dict = None) -> str:
    """Send a prompt to Gemini and return the response."""
    global active_chat

    if not session_start_time or active_chat is None:
        _start_new_session()

    conversation_history.append({"role": "user", "text": prompt, "action": action})

    try:
        response = active_chat.send_message(prompt)
        reply = response.text
        conversation_history.append({"role": "model", "text": reply})
        _save_session()
        return reply
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        conversation_history.pop()
        return f"❌ Gemini API error: `{e}`"


# ── Telegram Reply Helper ────────────────────────────────────────────────────
async def _reply(update: Update, text: str, parse_mode: str = "Markdown"):
    """Send a reply, chunking if needed, with fallback for parse errors."""
    if len(text) <= 4096:
        try:
            await update.message.reply_text(text, parse_mode=parse_mode)
        except Exception:
            await update.message.reply_text(text)
    else:
        for i in range(0, len(text), 4096):
            chunk = text[i : i + 4096]
            try:
                await update.message.reply_text(chunk, parse_mode=parse_mode)
            except Exception:
                await update.message.reply_text(chunk)


# ── Command Handlers — AI Chat ──────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await _reply(update,
        "🤖 *anti-bot — Antigravity Bridge v2*\n\n"
        "💬 *AI Chat:*\n"
        "  Just type anything → Gemini responds\n"
        "  `/ask <prompt>` — explicit query\n"
        "  `/clear` — fresh session\n"
        "  `/history` — session summary\n\n"
        "📂 *File Operations:*\n"
        "  `/view <path>` — read a file\n"
        "  `/edit <path> <instructions>` — AI-powered edit\n"
        "  `/create <path> <description>` — generate a file\n"
        "  `/ls [path]` — list directory\n"
        "  `/project [path]` — set working dir\n\n"
        "🚀 *Agentic Tasks:*\n"
        "  `/task <description>` — multi-step coding task\n\n"
        "🖥 *Mac Control:*\n"
        "  `/run <cmd>` — shell command\n"
        "  `/file <path>` — download file\n"
        "  `/screen` — screenshot\n"
        "  `/status` — health check\n\n"
        "📝 All actions logged for IDE continuity."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await cmd_start(update, context)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    _start_new_session()
    await _reply(update,
        "🗑 Session cleared. Starting fresh.\n"
        "Previous sessions are saved in `sessions/`."
    )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    if not conversation_history:
        await _reply(update, "📭 No conversation yet. Just type something!")
        return

    msg_count = len([m for m in conversation_history if m["role"] == "user"])
    action_count = len([m for m in conversation_history if m.get("action")])
    await _reply(update,
        f"📝 *Session:* `{session_start_time}`\n"
        f"💬 {msg_count} messages\n"
        f"📌 {action_count} actions logged\n"
        f"📁 Project: `{current_project_dir}`\n\n"
        f"Session saved to:\n`sessions/session_{session_start_time}.md`"
    )


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await _reply(update, "⚠️ Usage: `/ask <your question>`")
        return
    await _handle_ai_message(update, prompt)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text — check for approval or treat as AI conversation."""
    if not is_authorized(update):
        return

    text = update.message.text.strip()
    if not text:
        return

    global pending_action

    # Check if this is an approval response
    if pending_action and text in ("✅", "yes", "y", "approve"):
        await _execute_pending_action(update)
        return
    elif pending_action and text in ("❌", "no", "n", "reject", "cancel"):
        action_type = pending_action.get("type", "unknown")
        pending_action = None
        await _reply(update, f"🚫 {action_type} cancelled.")
        return

    await _handle_ai_message(update, text)


async def _handle_ai_message(update: Update, prompt: str) -> None:
    await _reply(update, "🧠 Thinking...")
    reply = await _ask_gemini(prompt)
    await _reply(update, reply)


# ── File Operations ──────────────────────────────────────────────────────────
async def cmd_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /project [path] — set or show working directory."""
    if not is_authorized(update):
        return

    global current_project_dir

    if not context.args:
        await _reply(update, f"📁 Current project: `{current_project_dir}`")
        return

    path = _resolve_path(" ".join(context.args))

    if not os.path.isdir(path):
        await _reply(update, f"❌ Not a directory: `{path}`")
        return

    current_project_dir = path
    _log_action("PROJECT_SET", {"path": path})
    conversation_history.append({
        "role": "user", "text": f"/project {path}",
        "action": {"type": "PROJECT_SET", "path": path},
    })
    _save_session()
    await _reply(update, f"📍 Project set to: `{path}`")


async def cmd_ls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ls [path] — list directory contents."""
    if not is_authorized(update):
        return

    path = _resolve_path(" ".join(context.args)) if context.args else current_project_dir

    if not os.path.isdir(path):
        await _reply(update, f"❌ Not a directory: `{path}`")
        return

    try:
        entries = sorted(os.listdir(path))
        # Format with icons
        lines = [f"📁 `{path}`\n"]
        for entry in entries[:50]:  # Limit to 50 entries
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                lines.append(f"  📂 {entry}/")
            else:
                size = os.path.getsize(full)
                if size < 1024:
                    sz = f"{size}B"
                elif size < 1024 * 1024:
                    sz = f"{size // 1024}KB"
                else:
                    sz = f"{size // (1024 * 1024)}MB"
                lines.append(f"  📄 {entry} ({sz})")

        if len(entries) > 50:
            lines.append(f"\n... and {len(entries) - 50} more")

        result = "\n".join(lines)
        _log_action("DIR_LIST", {"path": path, "count": len(entries)})
        await _reply(update, result, parse_mode=None)
    except PermissionError:
        await _reply(update, f"❌ Permission denied: `{path}`")


async def cmd_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /view <path> — read and display a file."""
    if not is_authorized(update):
        return

    if not context.args:
        await _reply(update, "⚠️ Usage: `/view <path>`")
        return

    path = _resolve_path(" ".join(context.args))

    if not os.path.exists(path):
        await _reply(update, f"❌ File not found: `{path}`")
        return

    if os.path.isdir(path):
        await _reply(update, "❌ That's a directory. Use `/ls` instead.")
        return

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        await _reply(update,
            f"❌ File too large ({size // 1024}KB). Max is {MAX_FILE_SIZE // 1024}KB.\n"
            f"Use `/file {path}` to download it instead."
        )
        return

    try:
        with open(path, "r") as f:
            content = f.read()

        # Add line numbers
        lines = content.split("\n")
        numbered = "\n".join(f"{i + 1:4d} │ {line}" for i, line in enumerate(lines))

        action = {"type": "FILE_VIEW", "file": path, "lines": len(lines)}
        _log_action("FILE_VIEW", {"file": path, "lines": len(lines)})
        conversation_history.append({
            "role": "user", "text": f"/view {path}",
            "action": action,
        })
        _save_session()

        header = f"📄 `{path}` ({len(lines)} lines)\n\n"
        await _reply(update, header + f"```\n{numbered}\n```")
    except UnicodeDecodeError:
        await _reply(update, "❌ Binary file — can't display. Use `/file` to download.")
    except Exception as e:
        await _reply(update, f"❌ Error reading file: `{e}`")


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /edit <path> <instructions> — AI-powered file edit."""
    if not is_authorized(update):
        return

    if not context.args or len(context.args) < 2:
        await _reply(update, "⚠️ Usage: `/edit <path> <edit instructions>`")
        return

    path = _resolve_path(context.args[0])
    instructions = " ".join(context.args[1:])

    if not os.path.exists(path):
        await _reply(update, f"❌ File not found: `{path}`")
        return

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        await _reply(update, f"❌ File too large ({size // 1024}KB).")
        return

    try:
        with open(path, "r") as f:
            original = f.read()
    except UnicodeDecodeError:
        await _reply(update, "❌ Binary file — can't edit.")
        return

    await _reply(update, f"🧠 Reading `{os.path.basename(path)}` and applying edits...")

    # Ask Gemini to edit the file
    edit_prompt = (
        f"I need you to edit the following file based on these instructions.\n\n"
        f"**File:** `{path}`\n"
        f"**Instructions:** {instructions}\n\n"
        f"**Current file contents:**\n```\n{original}\n```\n\n"
        f"Return ONLY the complete updated file contents, with no explanation "
        f"or markdown code fences. Just the raw file content."
    )

    new_content = await _ask_gemini(edit_prompt)

    # Clean up — Gemini sometimes wraps in code fences
    new_content = _strip_code_fences(new_content)

    # Generate diff
    diff = _generate_diff(original, new_content, path)

    if not diff.strip():
        await _reply(update, "ℹ️ No changes needed — file already matches.")
        return

    global pending_action
    pending_action = {
        "type": "FILE_EDIT",
        "path": path,
        "original": original,
        "new_content": new_content,
        "diff": diff,
        "instructions": instructions,
    }

    if REQUIRE_APPROVAL:
        preview = f"✏️ *Proposed edit to* `{os.path.basename(path)}`:\n\n```diff\n{diff[:3000]}\n```"
        if len(diff) > 3000:
            preview += "\n... (diff truncated)"
        preview += "\n\nReply ✅ to apply or ❌ to cancel."
        await _reply(update, preview)
    else:
        await _execute_pending_action(update)


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /create <path> <description> — AI-generated file."""
    if not is_authorized(update):
        return

    if not context.args or len(context.args) < 2:
        await _reply(update, "⚠️ Usage: `/create <path> <description of file>`")
        return

    path = _resolve_path(context.args[0])
    description = " ".join(context.args[1:])

    if os.path.exists(path):
        await _reply(update,
            f"⚠️ File already exists: `{path}`\n"
            f"Use `/edit` to modify it, or choose a different name."
        )
        return

    await _reply(update, f"🧠 Generating `{os.path.basename(path)}`...")

    # Ask Gemini to generate the file
    create_prompt = (
        f"Generate the contents of a new file.\n\n"
        f"**File path:** `{path}`\n"
        f"**Description:** {description}\n\n"
        f"Return ONLY the complete file contents, with no explanation "
        f"or markdown code fences. Just the raw file content."
    )

    content = await _ask_gemini(create_prompt)
    content = _strip_code_fences(content)

    global pending_action
    pending_action = {
        "type": "FILE_CREATE",
        "path": path,
        "content": content,
        "description": description,
    }

    if REQUIRE_APPROVAL:
        preview = f"🆕 *New file:* `{os.path.basename(path)}`\n\n```\n{content[:3000]}\n```"
        if len(content) > 3000:
            preview += "\n... (content truncated)"
        preview += "\n\nReply ✅ to create or ❌ to cancel."
        await _reply(update, preview)
    else:
        await _execute_pending_action(update)


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /task <description> — multi-step agentic coding task."""
    if not is_authorized(update):
        return

    if not context.args:
        await _reply(update, "⚠️ Usage: `/task <describe what you want to build/fix>`")
        return

    description = " ".join(context.args)

    await _reply(update, "🚀 Planning task...")

    # Get project context
    project_files = ""
    try:
        entries = os.listdir(current_project_dir)[:30]
        project_files = "\n".join(f"  - {e}" for e in sorted(entries))
    except Exception:
        project_files = "(could not list directory)"

    plan_prompt = (
        f"I need you to plan a coding task. Here's the context:\n\n"
        f"**Task:** {description}\n"
        f"**Working directory:** `{current_project_dir}`\n"
        f"**Files in project:**\n{project_files}\n\n"
        f"Create a step-by-step plan. For each step, indicate:\n"
        f"1. What action to take (create file, edit file, run command)\n"
        f"2. Which file or command\n"
        f"3. What the change does\n\n"
        f"Keep the plan concise. I'll execute it step by step.\n"
        f"Format each step as: `STEP N: [ACTION] [target] — [description]`"
    )

    plan = await _ask_gemini(plan_prompt)

    global pending_action
    pending_action = {
        "type": "TASK_EXECUTE",
        "description": description,
        "plan": plan,
    }

    await _reply(update,
        f"🚀 *Task Plan:*\n\n{plan}\n\n"
        f"Reply ✅ to execute or ❌ to cancel."
    )


# ── Pending Action Executor ──────────────────────────────────────────────────
async def _execute_pending_action(update: Update) -> None:
    """Execute the pending approved action."""
    global pending_action

    if not pending_action:
        await _reply(update, "ℹ️ No pending action to approve.")
        return

    action = pending_action
    pending_action = None
    action_type = action["type"]

    if action_type == "FILE_EDIT":
        path = action["path"]
        try:
            # Create backup
            backup_path = path + ".bak"
            with open(path, "r") as f:
                original = f.read()
            with open(backup_path, "w") as f:
                f.write(original)

            # Write new content
            with open(path, "w") as f:
                f.write(action["new_content"])

            _log_action("FILE_EDIT", {
                "file": path,
                "instructions": action["instructions"],
                "diff": action["diff"],
                "status": "✅ Applied",
            })
            conversation_history.append({
                "role": "user", "text": f"✅ Approved edit to {path}",
                "action": {
                    "type": "FILE_EDIT",
                    "file": path,
                    "diff": action["diff"],
                    "status": "✅ Applied",
                },
            })
            _save_session()

            await _reply(update,
                f"✅ *Edit applied* to `{os.path.basename(path)}`\n"
                f"Backup saved as `{os.path.basename(backup_path)}`"
            )
        except Exception as e:
            await _reply(update, f"❌ Error writing file: `{e}`")

    elif action_type == "FILE_CREATE":
        path = action["path"]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(action["content"])

            _log_action("FILE_CREATE", {
                "file": path,
                "description": action["description"],
                "content_preview": action["content"][:500],
                "status": "✅ Created",
            })
            conversation_history.append({
                "role": "user", "text": f"✅ Approved create {path}",
                "action": {
                    "type": "FILE_CREATE",
                    "file": path,
                    "status": "✅ Created",
                },
            })
            _save_session()

            await _reply(update, f"✅ *Created* `{os.path.basename(path)}`")
        except Exception as e:
            await _reply(update, f"❌ Error creating file: `{e}`")

    elif action_type == "TASK_EXECUTE":
        await _reply(update, "🚀 *Executing task step by step...*\n")

        exec_prompt = (
            f"Now execute this plan by providing the actual file contents and commands.\n\n"
            f"**Plan:**\n{action['plan']}\n\n"
            f"**Working directory:** `{current_project_dir}`\n\n"
            f"For each step, provide the COMPLETE implementation. "
            f"Format your response as a series of blocks:\n\n"
            f"For file creates/edits, use:\n"
            f"FILE: <path>\n"
            f"```\n<complete file contents>\n```\n\n"
            f"For commands, use:\n"
            f"RUN: <command>\n\n"
            f"Implement every step fully."
        )

        result = await _ask_gemini(exec_prompt)

        _log_action("TASK_EXECUTE", {
            "description": action["description"],
            "plan": action["plan"],
            "result": result[:2000],
            "status": "✅ Executed",
        })
        conversation_history.append({
            "role": "user", "text": f"✅ Approved task: {action['description']}",
            "action": {
                "type": "TASK_EXECUTE",
                "description": action["description"],
                "status": "✅ Executed",
            },
        })
        _save_session()

        await _reply(update, result)
        await _reply(update,
            "\n📝 *Task complete.* Results logged for IDE continuity.\n"
            "Use `/view`, `/edit`, or `/run` to follow up on individual steps."
        )


# ── Utility Functions ────────────────────────────────────────────────────────
def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from Gemini output."""
    text = text.strip()
    if text.startswith("```"):
        # Remove first line (```language)
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
    if text.endswith("```"):
        text = text[:-3].rstrip()
    return text


def _generate_diff(original: str, new: str, filename: str) -> str:
    """Generate a unified diff between two strings."""
    orig_lines = original.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines, new_lines,
        fromfile=f"a/{os.path.basename(filename)}",
        tofile=f"b/{os.path.basename(filename)}",
        lineterm="",
    )
    return "\n".join(diff)


# ── Mac Control Commands ─────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        f"📝 Session: `{session_start_time}`"
        if session_start_time
        else "📝 No active session"
    )

    await _reply(update,
        f"✅ *Online*\n\n"
        f"🖥 `{hostname}`\n"
        f"⏱ {uptime}\n"
        f"🔋 {battery}\n\n"
        f"📁 Project: `{current_project_dir}`\n"
        f"{session_info}"
    )


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    command = " ".join(context.args) if context.args else ""
    if not command:
        await _reply(update, "⚠️ Usage: `/run <command>`")
        return

    await _execute_and_reply(update, command)


async def cmd_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    path = " ".join(context.args) if context.args else ""
    if not path:
        await _reply(update, "⚠️ Usage: `/file <path>`")
        return

    path = _resolve_path(path)

    if not os.path.exists(path):
        await _reply(update, f"❌ File not found: `{path}`")
        return

    if os.path.isdir(path):
        await _reply(update, "❌ That's a directory. Use `/ls` instead.")
        return

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > 50:
        await _reply(update, f"❌ File too large ({size_mb:.1f} MB). Telegram limit is 50 MB.")
        return

    await update.message.reply_document(
        document=open(path, "rb"),
        filename=os.path.basename(path),
        caption=f"📁 `{path}`",
        parse_mode="Markdown",
    )


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    await _reply(update, "📸 Taking screenshot...")

    try:
        subprocess.run(
            ["screencapture", "-x", SCREENSHOT_PATH],
            timeout=10, check=True,
        )
        if os.path.exists(SCREENSHOT_PATH):
            await update.message.reply_photo(
                photo=open(SCREENSHOT_PATH, "rb"),
                caption="🖥 Screenshot",
            )
            os.remove(SCREENSHOT_PATH)
        else:
            await _reply(update, "❌ Screenshot failed.")
    except subprocess.TimeoutExpired:
        await _reply(update, "❌ Screenshot timed out.")
    except Exception as e:
        await _reply(update, f"❌ Screenshot error: `{e}`")


# ── Shell Execution Helper ───────────────────────────────────────────────────
async def _execute_and_reply(update: Update, command: str) -> None:
    if is_blocked(command):
        await _reply(update,
            f"🚫 *Blocked* — safety blocklist:\n`{command}`"
        )
        return

    logger.info(f"Executing: {command}")
    await _reply(update, f"⏳ Running: `{html.escape(command)}`", parse_mode="HTML")

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=COMMAND_TIMEOUT, cwd=current_project_dir,
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

        # Log the action
        action = {
            "type": "COMMAND_RUN",
            "command": command,
            "exit_code": exit_code,
            "output": (stdout or stderr or "(no output)")[:500],
        }
        _log_action("COMMAND_RUN", action)
        conversation_history.append({
            "role": "user", "text": f"/run {command}",
            "action": action,
        })
        _save_session()

        await _reply(update, response)

    except subprocess.TimeoutExpired:
        await _reply(update, f"⏰ *Timed out* after {COMMAND_TIMEOUT}s:\n`{command}`")
    except Exception as e:
        await _reply(update, f"❌ *Error:*\n`{e}`")


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("❌ ERROR: Set TELEGRAM_BOT_TOKEN in config.py first!")
        sys.exit(1)

    if not ALLOWED_CHAT_ID:
        print("❌ ERROR: Set ALLOWED_CHAT_ID in config.py first!")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("❌ ERROR: Set GEMINI_API_KEY in config.py or env!")
        sys.exit(1)

    _ensure_sessions_dir()
    _start_new_session()

    print("🤖 anti-bot v2 starting (Antigravity Bridge)...")
    print(f"   Authorized chat ID: {ALLOWED_CHAT_ID}")
    print(f"   Gemini model: {GEMINI_MODEL}")
    print(f"   Project dir: {current_project_dir}")
    print(f"   Sessions dir: {SESSIONS_DIR}")
    print("   Waiting for commands...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # AI chat
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("history", cmd_history))

    # File operations
    app.add_handler(CommandHandler("view", cmd_view))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("create", cmd_create))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(CommandHandler("project", cmd_project))

    # Agentic tasks
    app.add_handler(CommandHandler("task", cmd_task))

    # Mac control
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("file", cmd_file))
    app.add_handler(CommandHandler("screen", cmd_screen))

    # Plain text → AI chat (or approval handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
