#!/usr/bin/env bash
# ============================================================================
# Tour Assistant Update Script v2
# Synchronisiert Änderungen aus dem GitHub-Repo in die lokale
# Hermes-Installation.
#
# Usage:
#   bash scripts/tour-assistant-update.sh
#
# Das Repository wird als Quellverzeichnis verwendet.
# ============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_DIR="$HOME/.hermes/skills"

echo "📦 Tour Assistant Update v2"
echo "==========================="
echo ""

if [ ! -d "$REPO_DIR" ]; then
    echo "❌ Repo nicht gefunden unter: $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

echo "🔄 Pull von GitHub..."
git pull 2>&1 || echo "⚠️  Git-Pull fehlgeschlagen, sync trotzdem lokal..."
echo ""

# Skills komplett ersetzen (mit Backup alter Versionen)
for skill in outdoor-tour-assistant live-location-nearby location-session-core city-walk-guide; do
    target="$SKILLS_DIR/$skill"
    if [ -d "$target" ]; then
        backup="$target.backup-$(date +%Y%m%d-%H%M%S)"
        echo "📦 Backup: $skill → $backup"
        mv "$target" "$backup"
    fi
    echo "📄 Installiere: $skill"
    cp -R "skills/$skill" "$target"
done

# Gate-Wrapper neu schreiben (mit hardcodierten Env-Vars für Cron)
echo "📄 Schreibe Gate-Wrapper neu..."
cat > "$HOME/.hermes/scripts/live_tour_gate.py" << 'GATEWRAPPER'
#!/usr/bin/env python3
"""Wrapper: runs the canonical gate script from the skill directory.

This wrapper is copied to ~/.hermes/scripts/ and called by the cron job.
It sets up the Python path and environment for the skill's gate script.
"""

import os
import sys
from pathlib import Path

SKILL_SCRIPTS = Path.home() / ".hermes" / "skills" / "outdoor-tour-assistant" / "scripts"
CORE_SCRIPTS = Path.home() / ".hermes" / "skills" / "location-session-core" / "scripts"

os.environ.setdefault("HERMES_TOUR_CHAT_ID", "DEINE_TELEGRAM_CHAT_ID")
os.environ.setdefault("HERMES_TOUR_ACTIVITY", "cycling")
os.environ.setdefault("HERMES_TOUR_LOCALE", "de-DE")
os.environ.setdefault("HERMES_LOCATION_CORE_DIR", str(CORE_SCRIPTS))

for p in [str(SKILL_SCRIPTS), str(CORE_SCRIPTS)]:
    if p not in sys.path:
        sys.path.insert(0, p)

gate_script = SKILL_SCRIPTS / "live_tour_gate.py"
if not gate_script.exists():
    print('{"wakeAgent": false}')
    sys.exit(0)

exec(compile(gate_script.read_text(encoding="utf-8"), gate_script, "exec"))
GATEWRAPPER
echo "   ✅ Gate-Wrapper aktualisiert"

echo ""
echo "✅ Skills installiert!"
echo ""
echo "👉 Nächste Schritte:"
echo "   1. Setze Umgebungsvariablen in ~/.hermes/config.yaml oder ~/.zshrc:"
echo "      export HERMES_TOUR_CHAT_ID=\"DEINE_TELEGRAM_CHAT_ID\""
echo "      export HERMES_TOUR_ACTIVITY=\"cycling\""
echo "      export HERMES_TOUR_LOCALE=\"de-DE\""
echo ""
echo "   2. Update den Cron-Prompt (falls geändert)"
echo "   3. Starte den OwnTracks Receiver (falls benötigt):"
echo "      bash scripts/owntracks-start.sh"
echo "   4. Sag mir Bescheid, dass ich alles aktivieren soll"