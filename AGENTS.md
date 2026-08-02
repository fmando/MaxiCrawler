# AGENTS.md

## Projekt: MaxiCrawler

Dieses Dokument beschreibt die Arbeitsregeln für KI-Assistenten und
Mitwirkende.

### Architektur

-   Clean Architecture
-   `src`-Layout
-   Python 3.12+
-   Typannotationen verpflichtend
-   Tests für neue Funktionalität

### Codierregeln

-   Keine zyklischen Abhängigkeiten.
-   Domänenmodelle möglichst unveränderlich.
-   Infrastruktur ist austauschbar.
-   Öffentliche APIs dokumentieren.

### Pull Requests

-   Ein Feature pro PR.
-   Ruff, mypy und pytest müssen bestehen.
-   README und Dokumentation aktualisieren, falls nötig.

### Sprint-Prinzip

1.  Architektur vor Implementierung.
2.  Testbarkeit vor Optimierung.
3.  Erweiterbarkeit vor Spezialisierung.

### Rollen

-   Claude Code / Codex: Implementierung.
-   ChatGPT: Architektur, Review und Sprintplanung.
-   Mensch: Entscheidungen und Merge.

###   Language Policy

Conversation with AI assistants:
- German preferred.

Repository:
- English only.

Code:
- English only.

Documentation:
- English only.

Commit messages:
- English only.

Pull Requests:
- English only.
