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

## Erweiterungsschichten

Zwei Schichten erweitern MaxiCrawler um Wissen über fremde Hosts. Sie
beantworten unterschiedliche Fragen und sind deshalb getrennt:

| Schicht | Paket | Frage | I/O |
| --- | --- | --- | --- |
| Plugin | `maxicrawler.plugins` | *"Can I classify this URL?"* | keins |
| Provider | `maxicrawler.providers` | *"What can I do with this resource?"* | Netzwerk erlaubt |

Die Nahtstelle zwischen beiden ist `UrlClassification`. Ein Plugin entscheidet
allein anhand der URL-Zeichenkette und läuft deshalb bei jeder Discovery mit.
Ein Provider darf den Host befragen und wird nur von Befehlen aufgerufen, die
ausdrücklich Netzwerkzugriff vorsehen.

Provider dürfen Plugins importieren (etwa den reinen URL-Parser eines Hosts),
Plugins niemals Provider.

## Regeln

-   Domain kennt keine Infrastruktur.
-   Netzwerkzugriffe nur in der Infrastruktur.
-   Plugins kommunizieren über definierte Schnittstellen.
-   Plugins führen kein I/O aus; Provider führen I/O nur über `HttpTransport` aus.
-   Zugangsdaten aus einer URL bleiben im Prozess: `ResourceSecret` wird nie
    gerendert, protokolliert, gesendet oder gespeichert.
-   Ereignisse werden über den EventBus veröffentlicht.
-   Neue Features benötigen Tests.

## Qualitätsregeln

-   Ruff, mypy und pytest müssen erfolgreich sein.
-   Kleine, thematisch saubere Pull Requests.
-   `Squash and merge` für alle PRs.
