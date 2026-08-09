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
4.  Interface (CLI, Weboberfläche `maxicrawler.api`, GUI)

## Verarbeitungskette

```text
Website → Crawler → Discovery → Plugin → Provider → Download Manager → Library
```

Jede Station beantwortet genau eine Frage:

| Station | Paket | Frage | I/O |
| --- | --- | --- | --- |
| Crawl Engine | `maxicrawler.web.engine` | *"Which page comes next?"* | delegiert |
| Crawler | `maxicrawler.web` | *"Which URLs does this page contain?"* | Netzwerk |
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

Der Crawler ist ebenfalls keine Erweiterungsschicht. Er kennt weder Plugins
noch Provider: er holt ein Dokument, findet die URLs darin und übergibt sie
unverändert an die Discovery-Pipeline. Ein auf einer Webseite gefundener Link
wird deshalb von genau denselben Plugins klassifiziert wie einer aus einer
lokalen Datei.

## Clients

MaxiCrawler hat zwei Benutzerschnittstellen und **eine** Anwendungslogik. Beide
gehen durch `maxicrawler.app` — den Composition Root, die einzige Schicht, die
`config`, `database`, `web` und `crawler` gleichzeitig kennen darf:

```text
maxicrawler.cli ─┐
                 ├─→ maxicrawler.app ─→ web / crawler / database / plugins
maxicrawler.api ─┘
```

Die CLI bleibt vollständig erhalten und ist der Client für Automatisierung,
Skripte und Tests. Die Weboberfläche ist der Client zum Hinschauen und soll
langfristig die primäre Oberfläche werden. Sie enthält keine Crawl-Logik, keine
Kopie eines CLI-Renderers und keinen zweiten Objektgraphen; jeder Crawl, den sie
startet, wird von `CrawlService` gebaut.

Die Weboberfläche ist ein **optionales** Extra (`pip install "maxicrawler[web]"`).
Ohne sie funktioniert jeder Befehl außer `serve`, und `serve` erklärt in einem
Satz, was fehlt.

## Ausblick: Crawl Jobs

Eine `CrawlSession` beschreibt heute genau einen Crawl-Lauf. Die Weboberfläche
verwaltet Läufe bereits als Jobs, aber nur im Arbeitsspeicher: nach einem
Neustart bleibt, was in der Datenbank steht. Ein **Crawl Job** als eigene,
gespeicherte Einheit steht deshalb weiter aus:

```text
Job
 ├── CrawlSession      welcher Seed, welche Grenzen, wie es endete
 ├── Discovery         welche URLs dabei gefunden und klassifiziert wurden
 ├── Download Queue    was davon geholt werden soll
 └── Result            was am Ende in der Library liegt
```

Die interne Klasse behält ihren Namen. Ein Job ist eine Klammer *um* Session,
Discovery und Downloads, keine Umbenennung einer von ihnen — und keine der
vier Stationen muss dafür etwas voneinander wissen, was sie heute nicht schon
weiß.

## Regeln

-   Domain kennt keine Infrastruktur.
-   Netzwerkzugriffe nur in der Infrastruktur.
-   Der Crawler kennt keinen Provider, keinen Download und keine Library. Seine
    einzige Aufgabe ist *"Dokument holen und URLs finden"*.
-   Ein Abruf ist in jeder Dimension begrenzt: Schema, Umleitungen, Content-Type,
    Antwortgröße vor **und** nach dem Entpacken.
-   Höflichkeit ist ein Policy-Objekt, keine Bedingung in der Abrufschleife.
-   Der Crawler holt genau eine Seite. Rekursion ist Sache des Aufrufers — der
    `CrawlEngine` ist eine Schleife *über* dem Crawler, nie eine Änderung darin.
-   Der Frontier bestimmt die Reihenfolge, das VisitedSet die Identität. Nie
    beides in einer Klasse.
-   Der Schlüssel für „schon geholt" ist nicht der für „schon gefunden":
    Discovery behält Fragmente, der Frontier verwirft sie.
-   Eine `CrawlSession` beschreibt den Lauf. Wie Anfragen gestellt werden —
    Header, später Cookies, Zugangsdaten, Proxy — trägt der `RequestContext`,
    und nichts, was einen Report serialisiert, schreibt ihn.
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
-   Jeder Client geht durch `maxicrawler.app`. Eine zweite Oberfläche ist nie
    eine zweite Implementierung; gemeinsame Logik wird herausgezogen, nicht
    kopiert.
-   `maxicrawler.api` importiert weder `providers` noch `downloader` noch
    `library` und baut keinen Crawl-Objektgraphen selbst.
-   Kein Kernpaket importiert `maxicrawler.api`. Einzige Ausnahme ist
    `maxicrawler.cli`, weil dort `serve` liegt, und dort auch nur `api.errors`.
-   Diese Grenzen werden gelesen, nicht geglaubt: `tests/test_api_boundaries.py`
    prüft den Importgraphen.
-   Jede Seite der Weboberfläche funktioniert ohne JavaScript. Kein Build-System,
    kein npm, keine Ressource von einem fremden Host.
-   Neue Features benötigen Tests.

## Qualitätsregeln

-   Ruff, mypy und pytest müssen erfolgreich sein.
-   Kleine, thematisch saubere Pull Requests.
-   `Squash and merge` für alle PRs.
