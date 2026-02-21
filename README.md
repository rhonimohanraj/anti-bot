# 🤖 anti-bot — Telegram ↔ Antigravity IDE Bridge

Chat with Gemini from your phone via Telegram → pick up the conversation seamlessly in the Antigravity IDE on your Mac.

## Features

| Command | What it does |
|---|---|
| Just type anything | AI chat with Gemini |
| `/ask <prompt>` | Explicit AI query |
| `/run <command>` | Execute shell command on Mac |
| `/file <path>` | Download a file to your phone |
| `/screen` | Take a screenshot |
| `/status` | Mac health check |
| `/history` | Show session summary |
| `/clear` | Start a fresh session |

## Architecture

```
📱 Telegram → 🤖 anti-bot (runs on Mac) → ☁️ Gemini API
                    ↓
              💾 Session Logs → 🖥 Antigravity IDE
```

- **Works when Mac is off** — AI chat uses Gemini API, not local IDE
- **Conversations saved** as markdown in `sessions/` for IDE continuity
- **Parallel operation** — doesn't interfere with Antigravity IDE

## Setup

1. **Clone & install:**
   ```bash
   git clone https://github.com/rhonimohanraj/anti-bot.git
   cd anti-bot
   cp config.example.py config.py
   # Edit config.py with your API keys
   pip3 install --user -r requirements.txt
   ```

2. **Configure** `config.py`:
   - `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `ALLOWED_CHAT_ID` — from [@userinfobot](https://t.me/userinfobot)
   - `GEMINI_API_KEY` — from [Google AI Studio](https://aistudio.google.com/)

3. **Run:**
   ```bash
   python3 bot.py
   ```

4. **Auto-start on boot (optional):**
   ```bash
   bash install.sh
   ```

## Continuity

Sessions are saved to:
```
sessions/
├── session_2026-02-21_1430.md
├── session_2026-02-21_1800.md
└── latest.md                    ← always the most recent
```

In Antigravity IDE, say: *"Look at anti-bot/sessions/latest.md and continue from there"*

## License

MIT
