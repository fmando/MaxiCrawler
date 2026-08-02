# MaxiCrawler -- Architekturleitlinien

## Ziele

-   Modulare, erweiterbare Architektur
-   Klare Trennung von Domäne und Infrastruktur
-   Hohe Testbarkeit
-   Strikte Typisierung
-   Plugin-basierte Erweiterbarkeit

## Schichten

1.  Domain (`src/maxicrawler/domain`)
2.  Application (Discovery-Pipeline, Services)
3.  Infrastructure (SQLite, HTTP, Dateisystem)
4.  Interface (CLI, GUI, API)

## Regeln

-   Domain kennt keine Infrastruktur.
-   Netzwerkzugriffe nur in der Infrastruktur.
-   Plugins kommunizieren über definierte Schnittstellen.
-   Ereignisse werden über den EventBus veröffentlicht.
-   Neue Features benötigen Tests.

## Qualitätsregeln

-   Ruff, mypy und pytest müssen erfolgreich sein.
-   Kleine, thematisch saubere Pull Requests.
-   `Squash and merge` für alle PRs.
