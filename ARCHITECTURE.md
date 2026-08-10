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
`config`, `database`, `web`, `crawler`, `providers`, `downloader` und `library`
gleichzeitig kennen darf:

```text
                                        ┌─ CrawlService     → web / crawler / database
maxicrawler.cli ─┐                      ├─ DiscoveryService → database
                 ├─→ maxicrawler.app ─→ ├─ DownloadService  → providers / downloader / library
maxicrawler.api ─┘                      └─ LibraryService   → library
```

Die CLI bleibt vollständig erhalten und ist der Client für Automatisierung,
Skripte und Tests. Die Weboberfläche ist der Client zum Hinschauen und soll
langfristig die primäre Oberfläche werden. Sie enthält keine Crawl-Logik, keine
Download-Logik, keine Kopie eines CLI-Renderers und keinen zweiten
Objektgraphen; jeder Crawl, den sie startet, wird von `CrawlService` gebaut, und
jeder Download von `DownloadService`.

Ein Service gibt nach oben nur einfache Werte heraus — `DownloadProgress`,
`DownloadSummary`, `LibraryItem`, `LibraryPage`, `StoredPayload`, `MediaVerdict`.
Deshalb kann die Weboberfläche einen Transfer anzeigen, die Library durchsuchen
und eine gespeicherte Datei ausliefern, ohne `downloader`, `providers` oder
`library` zu importieren.

`DownloadService` schreibt in die Library, `LibraryService` liest sie. Zwei
Fragen an denselben Speicher, getrennt gehalten, damit keine der beiden das
Vokabular der anderen bekommt (ADR-028).

Dieselbe Trennung gilt seit Sprint 15 für die Discovery: `CrawlService`
schreibt, was ein Crawl gefunden hat, und `DiscoveryService` liest es zurück —
gesucht, gefiltert, sortiert und geblättert. Ein Report ist damit keine Ansicht
mehr, sondern eine Abfrage: `LinkQuery` hinein, `LinkPage` heraus. Dieselbe
Filtersprache beantwortet eine zweite Frage: `fetchable()` gibt `Matches`
zurück — die URLs, die der Filter trifft und die hier auch geholt werden
könnten. Zwei Fragen, ein Vokabular; eine Tabelle ist keine Menge. Ob ein Link
heruntergeladen werden kann, ist die eine Frage, die keine Datenbankspalte
beantwortet; sie kommt als Funktion herein, damit dieser Service weder Plugins
noch Provider kennen muss und eine spätere Frage derselben Form — *„liegt das
schon in der Library?"* — denselben Weg nimmt.

Die Weboberfläche ist ein **optionales** Extra (`pip install "maxicrawler[web]"`).
Ohne sie funktioniert jeder Befehl außer `serve`, und `serve` erklärt in einem
Satz, was fehlt.

## Verantwortungsvolles Crawlen

Seit Sprint 13 ist ein Crawl standardmäßig höflich, und zwar ohne dass eine der
bestehenden Stationen davon weiß. Alles davon wird in
`CrawlService.build_engine` zusammengesetzt — dem einzigen Ort, an dem es
zusammengesetzt wird:

```text
CrawlEngine ── policy ──→ PrivateNetworkPolicy (rein)         beim Finden
            └─ gate   ──→ PrivateNetworkPolicy (auflösend)    vor der Anfrage
                          RobotsPolicy ─────────────┐
                                                    │ delay_for
WebDiscoveryService ── ThrottledFetcher ────────────┘
                       └─ HostSchedule (geteilt)
                       └─ UrllibPageFetcher ── guard pro Weiterleitung
```

Jedes Teil ist für sich wirkungslos: eine Robots-Policy, die niemand fragt, eine
Drossel ohne Verzögerung, ein Netzwerkschutz, den niemand konsultiert. Beide
Clients bekommen das Verhalten, weil beide durch `maxicrawler.app` gehen — und
genau deshalb baut keiner von ihnen selbst eine Engine.

## Der erste vollständige Ablauf

Seit Sprint 11 führt die Weboberfläche die ganze Kette einmal durch:

```text
Crawl → Report → Download → Library
```

Seit Sprint 12 endet der Weg nicht in einer Tabelle: die Library wird durchsucht,
gefiltert, sortiert und geblättert, jede Datei hat eine Seite, und der Browser
zeigt an, was er anzeigen kann. MaxiCrawler rendert dabei nichts selbst — es
nennt einen Content-Type und übergibt die Bytes (ADR-027).

Der Download selbst brauchte dafür einen Service über dem Download Manager —
keinen zweiten Manager. Bis Sprint 14 lief davon bewusst genau einer zur Zeit,
ohne Warteschlange.

Seit Sprint 15 gibt es sie, und sie beantwortet drei der vier
Fragen, die ADR-026 offengelassen hatte: Reihenfolge (Ankunft, mit Buttons zum
Verschieben), Abbruch (wartend entfernen, laufend stoppen) und Pause (der
Warteschlange, nie des laufenden Transfers). Die vierte — Neustartfestigkeit —
bleibt offen, weil sie ohne echtes Resume nur anbieten könnte, dieselben Dateien
wieder bei null zu beginnen (ADR-033).

```text
TransferQueue (maxicrawler.api.downloads)   Aufträge: URLs, ungeplant
  └─ ein Worker ──→ DownloadService.download(url, on_progress, control)
                      └─ DownloadManager
                           └─ DownloadQueue (maxicrawler.downloader.queue)
                                            Jobs eines Plans, aufgelöst
```

Zwei Warteschlangen auf zwei Ebenen, absichtlich getrennt und absichtlich
verschieden benannt. Die obere entscheidet *Reihenfolge und Zeitpunkt* und
startet nichts selbst: jeder Transfer ist genau ein `DownloadService`-Aufruf.
Ein Worker, und das ist eine Höflichkeitsentscheidung, keine technische Grenze.

Seit Sprint 15 ist Downloads ein eigener Navigationsbereich mit einer Seite,
die die ganze Warteschlange zeigt. Sie hat **keinen eigenen Ereignisstrom**:
eingebettet ist der Strom des gerade laufenden Transfers, und `download.js`
lädt die Seite neu, wenn dieser endet — also genau dann, wenn sich der Rest der
Seite ändert. Eine Warteschlange, die niemand abarbeitet, hat nichts zu senden.

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
-   Eine Policy, die eine Anfrage stellen kann, wird genau einmal befragt:
    unmittelbar vor der Anfrage, die sie bewacht. Eine reine Policy wird beim
    Finden der URL befragt, damit der Frontier sauber bleibt. Beide Tore zählen
    über dieselbe Übersetzung, also gibt es weiterhin genau ein Vokabular für
    „warum nicht".
-   Warten ist keine Policy. *„Darf ich das holen?"* und *„darf ich es schon
    holen?"* sind zwei Fragen; die zweite gehört in einen `PageFetcher`-Decorator,
    und über ihm steht kein `sleep`.
-   robots.txt, Scope und Netzwerkschutz entscheidet ausschließlich die Policy.
    Weder Engine noch Fetcher erfahren, was eine robots-Regel oder eine interne
    Adresse ist; der Fetcher bekommt für jede Weiterleitung eine Funktion, die
    wirft, und keine Policy.
-   robots.txt regelt das Crawlen. Kein Provider fragt sie: ein Download ist eine
    ausdrückliche Handlung an einer benannten Ressource.
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
    `library` und baut weder einen Crawl- noch einen Download-Objektgraphen
    selbst.
-   `DownloadService` ist die einzige Stelle, die einen Download startet. Die
    Warteschlange entscheidet, welcher Auftrag als Nächstes drankommt und ob der
    Worker ihn nehmen darf — mehr nicht. Eine zweite Downloadlogik gibt es
    nicht, und eine Mehrfachauswahl ist keine: sie löst eine Auswahl in URLs auf
    und legt sie in dieselbe Warteschlange (ADR-034).
-   Eine Menge von Links wird bevorzugt *beschrieben* statt *aufgezählt*. „Alles
    was dieser Filter trifft" schickt die Abfrage und lässt den Server auflösen;
    nur angehakte Zeilen schicken URLs. Wo Elemente Zugangsdaten tragen, ist die
    beschreibende Form immer auch die sicherere.
-   Der Entschlüsselungsschlüssel eines Shares lebt in genau einem privaten
    Wörterbuch der Warteschlange. Kein Snapshot, keine Seite, kein Event-Frame
    und keine Weiterleitung trägt ihn; `tests/test_api_secret_confinement.py`
    liest das nach.
-   Ein laufender Download wird in der Senke abgebrochen, nicht im Manager und
    nicht im Provider: dort laufen die Bytes jedes Providers ohnehin vorbei, und
    dort ist bereits garantiert, dass ein unfertiger Transfer nichts hinterlässt.
    Ein abgebrochener Download schreibt kein Metadatendokument.
-   Ein Download aus dem Browser ist ausschließlich eine absolute HTTP(S)-URL.
    Ein Pfad wäre eine Aufforderung an den Server, auf fremden Klick die eigene
    Platte zu lesen; `DownloadService.require_url` ist die einzige Stelle, die
    das entscheidet.
-   Ein Eintragsschlüssel aus einer URL wird geprüft, bevor er ein Pfadsegment
    wird: `Library.entry_at` akzeptiert nur Komponenten, die dieses Projekt selbst
    erzeugt haben könnte, und weist alles ab, was die Wurzel verlässt — auch über
    einen Symlink.
-   MaxiCrawler rendert keine fremden Dateien. Was ein Browser angezeigt bekommen
    darf, steht in einer Tabelle in `maxicrawler.app.viewing`; `mimetypes` wird
    nie befragt, weil es unter Windows die Registry liest.
-   Was Skript ausführen kann — HTML und SVG — wird nur mit
    `Content-Security-Policy: sandbox` ausgeliefert. Ohne das hätte eine
    heruntergeladene Seite jede Befugnis dieser Oberfläche.
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
