# MaxiCrawler -- Architekturleitlinien

## Ziele

-   Modulare, erweiterbare Architektur
-   Klare Trennung von Domäne und Infrastruktur
-   Hohe Testbarkeit
-   Strikte Typisierung
-   Plugin-basierte Erweiterbarkeit

## Schichten

1.  Domain (`src/maxicrawler/domain`)
2.  Application (Discovery-Pipeline, Download Manager, Services)
3.  Infrastructure (SQLite, HTTP, Dateisystem, Library)
4.  Interface (CLI, GUI, API)

## Verarbeitungskette

```text
Website / URL → Discovery → Plugin → Provider → Download Manager → Library
```

Jede Station beantwortet genau eine Frage:

| Station | Paket | Frage | I/O |
| --- | --- | --- | --- |
| Discovery | `maxicrawler.crawler` | *"Which URLs exist?"* | Dateisystem |
| Plugin | `maxicrawler.plugins` | *"Can I classify this URL?"* | keins |
| Provider | `maxicrawler.providers` | *"What can I do with this resource?"* | Netzwerk erlaubt |
| Download Manager | `maxicrawler.downloader` | *"How are downloads executed?"* | delegiert |
| Library | `maxicrawler.library` | *"How are resources stored?"* | Dateisystem |

## Erweiterungsschichten

Zwei der Stationen erweitern MaxiCrawler um Wissen über fremde Hosts. Sie
beantworten unterschiedliche Fragen und sind deshalb getrennt.

Die Nahtstelle zwischen beiden ist `UrlClassification`. Ein Plugin entscheidet
allein anhand der URL-Zeichenkette und läuft deshalb bei jeder Discovery mit.
Ein Provider darf den Host befragen und wird nur von Befehlen aufgerufen, die
ausdrücklich Netzwerkzugriff vorsehen.

Provider dürfen Plugins importieren (etwa den reinen URL-Parser eines Hosts),
Plugins niemals Provider.

Download Manager und Library sind bewusst **keine** Erweiterungsschichten. Ein
neuer Host wird als Plugin und Provider ergänzt; an beiden ändert sich dabei
keine Zeile.

## Regeln

-   Domain kennt keine Infrastruktur.
-   Netzwerkzugriffe nur in der Infrastruktur.
-   Plugins kommunizieren über definierte Schnittstellen.
-   Plugins führen kein I/O aus; Provider führen I/O nur über `HttpTransport`
    und `StreamTransport` aus.
-   Zugangsdaten aus einer URL bleiben im Prozess: `ResourceSecret` wird nie
    gerendert, protokolliert, gesendet oder gespeichert.
-   Der Download Manager verzweigt nie über einen Providernamen. Unterschiede
    werden über das Provider-Protokoll erfragt oder über `ProviderCapability`
    deklariert.
-   Die Library kennt keinen Provider über dessen Namen hinaus und entscheidet
    als einzige Schicht einen Pfad für heruntergeladene Inhalte.
-   Jeder Name, der aus einer fremden Antwort stammt, wird über
    `maxicrawler.library.naming` geführt, bevor er ein Pfadsegment wird.
-   Ereignisse werden über den EventBus veröffentlicht.
-   Neue Features benötigen Tests.

## Qualitätsregeln

-   Ruff, mypy und pytest müssen erfolgreich sein.
-   Kleine, thematisch saubere Pull Requests.
-   `Squash and merge` für alle PRs.
