#!/bin/bash
# Install TimeSense LaunchAgent so it runs at login (no window).
# Run from the project root, or pass the project root as the first argument.
# If the agent fails to start (see /tmp/timesense-backend.err), move the project
# out of Desktop (e.g. to ~/Projects/AI Architect) and run this script again.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
ROOT="$(cd "$ROOT" && pwd)"

PLIST_NAME="com.zoeyzhou.timesense.backend"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS/$PLIST_NAME.plist"

# Write start script that uses this root
START_SCRIPT="$ROOT/scripts/start-timesense.sh"
mkdir -p "$ROOT/scripts"
cat > "$START_SCRIPT" << 'STARTSCRIPT'
#!/bin/bash
ROOT="ROOT_PLACEHOLDER"
cd "$ROOT" || exit 1
export PATH="$ROOT/.venv/bin:$PATH"
exec uvicorn main:app --host 127.0.0.1 --port 8000
STARTSCRIPT
sed -i '' "s|ROOT_PLACEHOLDER|$ROOT|g" "$START_SCRIPT"
chmod +x "$START_SCRIPT"

# Write plist
mkdir -p "$LAUNCH_AGENTS"
cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_NAME</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$START_SCRIPT</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/timesense-backend.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/timesense-backend.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin:$ROOT/.venv/bin</string>
  </dict>
</dict>
</plist>
PLIST

# Unload if already loaded, then load
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "TimeSense LaunchAgent installed and loaded."
echo "  Project root: $ROOT"
echo "  Plist:        $PLIST_PATH"
echo "  Logs:         /tmp/timesense-backend.log and .err"
echo ""
echo "Test in browser: http://127.0.0.1:8000"
echo "If the app does not load, check: tail -f /tmp/timesense-backend.err"
echo "On macOS, if you see 'Operation not permitted', move the project out of Desktop (e.g. mv to ~/Projects/AI\\ Architect) and run this script again from the new location."
