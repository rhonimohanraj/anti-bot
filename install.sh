#!/bin/bash
# ──────────────────────────────────────────────────────
# anti-bot — Install & Setup Script
# ──────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.antibot.telegram"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "🤖 anti-bot — Setup"
echo "══════════════════════════════════════"

# ── Step 1: Install Python dependencies ──
echo ""
echo "📦 Installing Python dependencies..."
pip3 install --user -r "$SCRIPT_DIR/requirements.txt"

# ── Step 2: Check config ──
echo ""
echo "🔧 Checking configuration..."

TOKEN=$(python3 -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); from config import TELEGRAM_BOT_TOKEN; print(TELEGRAM_BOT_TOKEN)")
CHAT_ID=$(python3 -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); from config import ALLOWED_CHAT_ID; print(ALLOWED_CHAT_ID)")

if [ -z "$TOKEN" ]; then
    echo ""
    echo "⚠️  You need to set up your Telegram bot token!"
    echo ""
    echo "   1. Open Telegram on your phone"
    echo "   2. Search for @BotFather and start a chat"
    echo "   3. Send: /newbot"
    echo "   4. Name it: anti-bot"
    echo "   5. Choose a username like: antibot_rhoni_bot"
    echo "   6. Copy the token BotFather gives you"
    echo "   7. Paste it into: $SCRIPT_DIR/config.py"
    echo ""
    echo "   Then re-run this script."
    exit 1
fi

if [ -z "$CHAT_ID" ]; then
    echo ""
    echo "⚠️  You need to set your Telegram chat ID!"
    echo ""
    echo "   1. Open Telegram on your phone"
    echo "   2. Search for @userinfobot and start a chat"
    echo "   3. It will reply with your chat ID (a number)"
    echo "   4. Paste it into: $SCRIPT_DIR/config.py"
    echo ""
    echo "   Then re-run this script."
    exit 1
fi

echo "   ✅ Bot token found"
echo "   ✅ Chat ID found: $CHAT_ID"

# ── Step 3: Install LaunchAgent (auto-start) ──
echo ""
read -p "🚀 Auto-start anti-bot when your Mac boots? (y/n): " AUTO_START
if [ "$AUTO_START" = "y" ] || [ "$AUTO_START" = "Y" ]; then
    # Generate plist with correct paths
    cat > "$PLIST_SRC" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$SCRIPT_DIR/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/antibot.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/antibot_error.log</string>
</dict>
</plist>
EOF

    cp "$PLIST_SRC" "$PLIST_DEST"
    launchctl load "$PLIST_DEST" 2>/dev/null || true
    echo "   ✅ LaunchAgent installed — anti-bot will auto-start on boot"
    echo "   📄 Log: $SCRIPT_DIR/antibot.log"
else
    echo "   ⏭️  Skipped auto-start"
fi

# ── Done ──
echo ""
echo "══════════════════════════════════════"
echo "✅ anti-bot setup complete!"
echo ""
echo "To start manually:"
echo "   cd $SCRIPT_DIR && python3 bot.py"
echo ""
echo "To stop the LaunchAgent:"
echo "   launchctl unload $PLIST_DEST"
echo ""
echo "To test: open Telegram and send /status to your bot"
echo "══════════════════════════════════════"
