---
name: 🐛 Bug Report
about: Einen Fehler melden, der den Tour-Assistenten betrifft
title: "[BUG] "
labels: bug
assignees: ''
---

**Beschreibung**
Kurze und präzise Beschreibung des Fehlers.

**Schritte zum Reproduzieren**
1. Skill installiert und Cron eingerichtet
2. Tour gestartet mit `...`
3. `tourctl.py context` liefert `...`
4. Fehler: ...

**Erwartetes Verhalten**
Was sollte passieren?

**Tatsächliches Verhalten**
Was ist stattdessen passiert?

**Logs & Diagnose**
```bash
python3 ${HERMES_SKILL_DIR}/scripts/tourctl.py diagnose
```

**Umgebung**
- Hermes Version:
- Betriebssystem:
- Skill-Version (aus SKILL.md):
- Python-Version:

**Wichtiger Hinweis**
Bitte keine Live-Koordinaten, Telegram-IDs oder private GPX-Dateien in das Issue einfügen. Nutze `tourctl.py context` (ohne `--include-location`) für den Kontext.