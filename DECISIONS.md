# Architecture Decisions (ADR)

## ADR-001: Clean Architecture

Separate domain logic from infrastructure to improve maintainability and
testing.

## ADR-002: Event Bus

Components communicate through events instead of direct dependencies.

## ADR-003: Plugin-first Design

Host-specific functionality belongs in plugins rather than the
application core.

## ADR-004: SQLite

SQLite is the default metadata store for portability and
zero-configuration.

## ADR-005: Quality Gates

Every pull request must pass Ruff, mypy and pytest before merging.

## ADR-006: Provider layer beside the plugin layer

Host knowledge is split across two layers instead of one. The plugin layer
answers *"can I classify this URL?"* from the URL string alone and stays free
of I/O, so it can run on every discovered URL. The provider layer answers
*"what can I do with this resource?"* and may contact the host.

Folding both into the plugin protocol would have made classification able to
block on the network, which the offline discovery workflow depends on not
doing. Providers may import plugins; the reverse is forbidden.

## ADR-007: Credentials are wrapped, never rendered

A share link can carry a decryption key in its URL fragment. `ResourceSecret`
holds such a value and exposes it only through an explicit `reveal()` call, so
every use is greppable and no accidental `repr()`, log record, exception, or
serialization can leak it. `ResourceRef` stores the share URL with the
fragment already removed.

The rule is enforced by tests rather than by convention: the suite scans every
outgoing request for fragments of the key and reads the syntax tree to confirm
that only the provider module unwraps a secret.

## ADR-008: Availability is a value, not an exception

A resource that was deleted, revoked, or blocked is a valid answer to the
provider layer's question, so it is reported as an `Availability` value.
Exceptions are reserved for failures on our side — a broken connection, an
unparsable response.

This keeps "the link is dead" distinguishable from "we could not find out",
which the CLI turns into two different exit codes.

## ADR-009: Cryptography is an optional extra

Decryption is needed only to read names, and only for providers that encrypt
them. `cryptography` is therefore an optional `[mega]` extra behind a
`CipherBackend` protocol. Without it, an inspection still reports sizes,
timestamps, and structure; a link that actually carries a key reports the
missing package instead of degrading silently.

## ADR-010: One directory per resource, namespaced by provider

The library stores each downloaded resource in its own directory under a
provider namespace, with a `metadata.json` beside a `content/` directory and an
`.incomplete/` staging directory:

```text
library/<provider>/<slug>-<digest>/{metadata.json, content/, .incomplete/}
```

The file system is the source of truth. Every entry describes itself, so a
library survives losing a database, can be moved with `rsync`, and stays
readable with a text editor. An index may be added later as a rebuildable
cache, never as the authority.

The payload lives in its own directory because a provider is free to name a
file `metadata.json`. Separating them makes that harmless rather than
destructive.

An entry is addressed by identity, not by name. The directory key is derived
from the reference alone — provider, container, resource — so a renamed remote
file, or one read through a link carrying no key, still finds the same entry.
The key keeps a readable stem and appends a ten-character digest: the stem
alone would merge `AbCdEfGh` and `abcdefgh` on a case-insensitive volume, which
is the default on Windows and macOS.

Rejected alternatives are recorded in
[docs/architecture.md](docs/architecture.md#why-this-layout-and-not-another).

## ADR-011: The download manager knows no provider

`maxicrawler.downloader` never branches on a provider name. Where behaviour
differs between hosts it is asked for through `ResourceProvider` or declared
through `ProviderCapability`. A provider transfers into a `DownloadSink` it
does not own, so it never learns where the bytes land; the manager never learns
how they were obtained.

The alternative — a manager that special-cases each host — would have made
every new provider a change to the orchestration layer, which is exactly the
coupling the plugin and provider layers were introduced to avoid.

## ADR-012: A partial download is never visible

Content is written under `.incomplete/` and moved into `content/` only once it
is whole and the announced size matches. A transfer that fails, is interrupted,
or arrives short leaves the library exactly as it was.

This is what makes "skip what is already there" safe. Skipping is decided from
the metadata record plus the presence of the payload file, so it can never be
fooled by a half-written file — and it needs no network request at all, which
is why re-running over a list of already-downloaded links contacts nobody.

Resume is deliberately not implemented. The staging file it would need already
exists, and the record it would extend already has room, so adding it later is
an addition rather than a redesign.

## ADR-013: Metadata is versioned and forward-compatible

Every `metadata.json` states a schema version. A document written by a newer
release is refused rather than misread, and members the current release does
not recognise are preserved verbatim across a round trip.

Without the first rule, an older binary would silently discard fields it does
not know. Without the second, a mixed-version environment would lose data on
every write. Both are cheap now and impossible to retrofit onto a library that
already holds thousands of entries.

## ADR-014: A web layer beside discovery, not inside it

`maxicrawler.web` is a package of its own rather than a module under
`maxicrawler.crawler`. The crawler package is documented throughout as the
station whose I/O is the file system and which never leaves the machine;
putting a socket inside it would erase a boundary the documentation currently
makes checkable.

The new layer answers one question — *"which URLs does this page contain?"* —
and imports `crawler`, `extractors`, `domain`, and `utils`. It imports no
provider, no downloader, and no library, which is a rule you can check by
reading: grep the package for `mega`, `provider`, `download`, or `library` and
there are no hits.

## ADR-015: The crawler has its own bounded fetcher

Neither transport in `maxicrawler.providers` fits a crawl. `post_json` sends
and returns JSON; `stream` yields bytes but exposes no status, no headers, and
no URL that finally answered — which are the three things a crawler needs
most. Reaching for either would also make the web layer depend on the provider
layer, which ADR-014 forbids.

`UrllibPageFetcher` therefore owns the request, and every limit it enforces is
there because the page belongs to a stranger:

-   the scheme allow-list keeps `file:`, `data:`, and `javascript:` targets
    away from a socket, on the first request and on every redirect hop;
-   redirects are capped, recorded, and restricted to HTTP(S) — the standard
    handler permits `ftp:` and keeps its chain to itself;
-   the content type is checked from the headers *before* the body is read, so
    a video answered to a page request costs one round trip rather than a
    download;
-   the size limit applies to the bytes as they arrive *and* to what a
    compressed body expands to, so a small archive that inflates to gigabytes
    is refused like a large one.

The two pieces genuinely shared with the provider transports — the scheme
guard and the URL redaction rule — moved down into `maxicrawler.utils.urls`
rather than being copied.

## ADR-016: Politeness is a policy object

`CrawlPolicy` has one method and one implementation that says yes to
everything. It exists before anything needs it because it is the seam that
robots.txt, scope rules, depth limits, private-network guards, and rate limits
all plug into, and a seam introduced after those grew is a redesign of all of
them.

`RobotsPolicy` will read `/robots.txt` through the same `PageFetcher` the
crawl already uses, so no second I/O seam is required. A refusal is a value
rather than an exception, so a recursive crawl records *"skipped: disallowed by
robots.txt"* against a URL and carries on; the service raises only for the URL
it was explicitly asked for, where refusing is a failure of the request.

robots.txt is deliberately not implemented yet. Fetching one page named by its
operator is what a browser does when the same person types the same address.

**Superseded in part by ADR-029 (Sprint 13).** robots.txt is implemented, on by
default, and parsed by Protego rather than by `urllib.robotparser` — which
compares paths with `startswith` and would silently under-obey. The seam
described here held: `RobotsPolicy` is a `CrawlPolicy`, reads through the same
`PageFetcher`, and neither the engine nor the fetcher learned what a robots rule
is. What changed is *which gate it is asked at* (ADR-030) and that the reason it
gives is a value a report counts rather than a sentence a reader parses.

## ADR-017: One page per call; recursion is a caller

`WebDiscoveryService.crawl()` takes one URL, returns one immutable
`CrawlResult`, and holds no state about which URL to visit next. Recursion is
therefore not a property of the crawler but a question of who calls it, in
what order — an addition *above* the layer rather than a change inside it.

What it will take, and why none of it is a redesign:

-   a **frontier**, which is `DownloadQueue` again — lock-guarded,
    deduplicating by identity, handing out one item at a time;
-   a **scope**, which is the `CrawlPolicy` of ADR-016;
-   a **scheduler**, which is a `PageFetcher` wrapping another `PageFetcher`,
    the same trick `Retrier` plays around the provider transport.

`CrawlResult` keeps both `requested_url` and `final_url` for this reason. A
redirect makes them differ, and a crawl queue, a crawl history, and a user
interface each need a different one of the two; deriving either later is
impossible once one has been dropped.

One honest caveat: `DiscoveryPipeline` is not thread-safe. Its statistics are
rebound without a lock and its duplicate detector holds a plain set, so
parallel crawling needs either a lock there or one pipeline per worker with
merged `Statistics`. That is a contained change to an existing class, and it
is recorded here rather than discovered later.

## ADR-018: The engine is a loop above the service, not a recursive service

Recursion lives in `maxicrawler.web.engine`, which calls
`WebDiscoveryService.crawl_page()` in a loop. The service still answers one
question — *"which URLs does this page contain?"* — and knows nothing about
frontiers, depth or scope.

The alternative, a `depth` parameter on the service, would have put frontier
state inside fetch → parse → discover. That is precisely what makes recursion a
redesign rather than an addition: a service that owns a visited set cannot be
called by a scheduler, cannot be driven by a queue it does not own, and cannot
be reused for a single page without carrying the machinery for many.

`crawl()` kept its signature and its meaning: a whole discovery session for one
page. `start()` / `crawl_page()` / `finish()` is the same thing taken apart, and
the engine is the only caller that needs the pieces.

## ADR-019: The visited key is not the discovery key

Two questions that look identical and are not:

| | Question | Key |
| --- | --- | --- |
| `DuplicateDetector` | *"have I **discovered** this URL?"* | fragment **kept** |
| `VisitedSet` | *"have I **fetched** this page?"* | fragment **removed** |

`normalize_url` preserves fragments because a legacy Mega share keeps its
handle and decryption key there (ADR-007). But `page#intro` and `page#setup`
are one page to fetch. A crawler sharing one key with discovery would either
fetch a page once per anchor in its own navigation menu, or destroy every Mega
link it found.

Enqueue-time identity is not sufficient on its own. When `/old` and `/new` are
both linked from a page, both are queued before anyone knows a redirect makes
them the same page, so pages that actually answered are tracked separately and
checked again when an item is popped.

`<link rel="canonical">` is recorded and never acted on. It is a claim by the
page, not a fact about the URL, and skipping a URL that was never fetched loses
every outgoing link on it. Content-level de-duplication belongs beside a
content fingerprint, which this project does not have yet.

## ADR-020: A session carries request context; a report never carries a credential

"Session" names two things in a crawler, and they are separate types here.
`CrawlSession` is the run — identity, seed, options, start time — and it is what
gets summarized, serialized and stored. `RequestContext` is how requests are
made, and it is the declared home of the cookie jar, credential and proxy that
come later.

The division with the fetcher is the other half. **The context holds the data; a
fetcher decorator holds the behaviour.** Performing a login, refreshing a CSRF
token or retrying a 401 belongs to something wrapping a `PageFetcher`, which is
already a protocol, so neither the engine nor the discovery service changes when
authentication arrives. The crawler never learns *how* it works.

A report can reach a context by traversal, so the enforceable rule is narrower
than "the report holds no credential": **nothing that serializes a report writes
the context.** Neither the JSON renderer nor the SQLite adapter reads it, and
each asserts that where it lives — the database file is searched for the secret,
and both modules' syntax trees are checked.

## ADR-021: The frontier orders, the visited set identifies

`Frontier` decides *what comes next* and nothing else. It does not deduplicate,
does not know about depth, and does not know about scope; by the time an item
reaches it, the engine's single gate has already decided the page will be
fetched. Mercator's crawler splits prioritisation from politeness for the same
reason.

A queue that also owned identity could not later be swapped for one that owns
only order — and a priority frontier, per-host politeness queues, a persistent
frontier and a distributed one are all changes to order alone.

One limitation is recorded rather than hidden: `pop()` returning `None` means
*"nothing left"*, and a scheduler needs to distinguish that from *"nothing
yet"*. That is one added member on the protocol and one branch in the loop.

The engine derives the scope from the session rather than trusting a caller to
inject a matching policy. An option that silently does nothing unless wired
correctly would let a report and a database row claim a crawl stayed on one
host while it wandered off it.

## ADR-022: The web interface is a second client, not a second implementation

The command line was the only client for nine sprints, and it had grown a
composition root inside itself: it built the pipeline, the fetcher, the
repositories and the engine, then rendered what came back. A browser client
could have been written the same way. It would have worked, and the two would
have drifted — a flag one of them honours, a default the other has, a bug fixed
once.

So the graph moved into `maxicrawler.app` first, the command line was changed to
use it, and only then did `api` exist. `CrawlService` runs a crawl for whoever
is asking and `crawl_document` turns a report into JSON; both clients call the
same function rather than agreeing on what it should say.

Three rules keep it that way, and `tests/test_api_boundaries.py` reads the
import graph rather than trusting the prose:

- `api` never imports `providers`, `downloader` or `library`. The library page
  lists nothing yet precisely because listing it will go through a service in
  `maxicrawler.app`, for the same reason crawling does.
- `api` imports nothing `CrawlService` assembles — no `CrawlEngine`, no
  `DiscoveryPipeline`, no fetcher, no repository. A second object graph is how
  two clients quietly become two crawlers.
- No core package imports `api`. `maxicrawler.cli` is the one exception,
  because the `serve` command lives there, and on import it reaches for
  `api.errors` alone.

The command line stays whole. It is the client for automation, scripting and
tests; the web interface is the one meant for looking at, and is intended to
become the primary one.

## ADR-023: Server-rendered HTML, and no build system

Starlette and Jinja2 render the pages. There is no React, no bundler, no npm and
no TypeScript; the browser loads one stylesheet and one script, both written by
hand and served out of the package.

That follows from what the thing is. This is an operator's console — tables,
counters, a progress line, a report — closer to Grafana or Proxmox than to an
application. A page of it is a table of numbers that the server already has, and
a round trip is enough to deliver them. A build system would add a toolchain, a
lockfile and a second language to a project whose point is that its parts stay
separable.

Starlette rather than FastAPI for the same reason: FastAPI earns its weight
through request-model validation and a generated OpenAPI document, and this
serves HTML with three form fields. It is also the layer FastAPI is built on, so
nothing is lost if a JSON API for other tools later makes FastAPI worth it.

**Progressive enhancement is the rule.** Every page is complete without
JavaScript. The live view is an `EventSource` that replaces numbers already
rendered, and reloading the page asks the server for the same numbers. With
scripting off the interface still works; it stops updating by itself, which is
the only thing the script does.

htmx was considered and its licence (0BSD) checked. It is deliberately not
vendored yet: the routes already render standalone fragments, which is the
expensive half of adopting it, and the cheap half can be added the day filters
and sorting need it.

## ADR-024: A crawl the browser starts is a background job

A crawl takes minutes; a request must not. `CrawlJobs` runs each crawl on a
worker thread and keeps an in-memory registry of them, so the form that starts
one redirects to its page immediately, and `/health` keeps answering while the
crawl runs — which is what that route is for.

Events cross back to the event loop through `loop.call_soon_threadsafe`, and the
stream coalesces: a subscriber gets the newest snapshot, not every event. A
crawler that finds four hundred links in a second must not become four hundred
messages on a socket, and nobody reading the page could tell the difference.

Every job builds its own object graph, because `DiscoveryPipeline` is not
thread-safe (ADR-022 put that graph in one place; the registry does nothing to
work around it). Two crawls at once are two graphs, exactly as two command-line
invocations are two processes.

The registry is memory, and says so. Restart the server and the jobs are gone —
the crawls are not, because they were stored. The pages then fall back to the
database, and a stored crawl that is not running in this process is called
`abandoned` rather than left looking like it is still going. What the database
does not hold — the page table, the skip reasons — the page reports as not
recorded, instead of drawing an empty table that reads as a zero.

## ADR-025: The interface has no authentication, so it listens on loopback

Anyone who can reach the port can start a crawl, and a crawl is an outbound
request made from this machine and charged to its address. On `127.0.0.1` that
means whoever is already logged in here, which is the situation the command line
is in anyway. On a network it means something else.

So `maxicrawler serve` binds `127.0.0.1` by default and refuses anything else
unless `--allow-remote` says so in as many words, with the reason and the flag in
the refusal. A hostname is treated as remote without consulting a resolver: a
name can point anywhere, and can start pointing somewhere else tomorrow, so
asking is the cheap mistake. When the flag is given, the warning is printed on
every start rather than once, because the flag is typed once and the terminal is
read afterwards.

This is not security, and is not offered as any. A flag stops nobody determined;
it is the difference between exposing a service and exposing one by accident,
which is the failure that actually happens. Real exposure wants a reverse proxy
that authenticates in front of it, and until the interface has accounts of its
own, binding anywhere else stays a deliberate act.

## ADR-026: One download service, and one download at a time

The download graph — a provider registry with a stream transport, a library at
the configured root, a manager around both — was assembled inside the `download`
command, exactly as the crawl graph had been assembled inside `crawl` before
Sprint 10. ADR-022 records what that produced: two clients quietly becoming two
implementations. So `DownloadService` was extracted first, the command line was
changed to use it, and only then did the browser learn to download.

The service composes and reports; it transfers nothing. `DownloadManager` is
unchanged, and so are the planner, the queue, the worker, the sink and the
library. What is new above them is a vocabulary a client can use without
importing the download layer at all — `DownloadProgress` while a transfer runs,
`DownloadSummary` when it is over, `LibraryItem` for what is stored. That is
what lets the library page list real files while `api` still imports neither
`downloader`, nor `providers`, nor `library`, which
`tests/test_api_boundaries.py` reads rather than believes.

**One at a time, and no queue.** A second request while a transfer is running is
refused with a message naming the one that is running. A queue needs a policy
for ordering, cancelling, resuming and surviving a restart, and none of that is
worth inventing before a single download works end to end. Everything the
refusal costs is one click later.

**A browser may name a URL, never a path.** `SourceResolver` treats anything
that is not an HTTP(S) URL as a file or a directory and reads it for the links
inside, which is right for a command line and would be a way to make the server
read its own disk on somebody else's click. `DownloadService.require_url` is the
one place that decides this, and it is checked before a worker thread starts, so
a bad link is a message rather than a run that exists only to have failed.

**The key stays in the body.** A Mega share carries its decryption key in the
URL fragment, which a browser never transmits as part of a URL — and does
transmit as a form field. So the Download button is a form, the link travels in
the body, and everything downstream of it holds the fragment-free URL: the run,
every snapshot, every page, every event frame. Nothing is written into a query
string, a redirect or a log.

**A transfer is described before it starts.** The planner asks nobody about a
plain file link, because a run over two hundred links must not become two
hundred extra requests. For one deliberate click the trade is the other way, so
the service plans with `inspect_files=True`: one request buys the file's name
and size, which is the difference between "Jump.pdf, 1.3 MB" and a bare handle
under a progress bar with no denominator. A transfer whose size nobody states
still gets an indeterminate bar rather than one stuck at zero.

**There is no Stop button yet, and the shutdown says so.** A crawl checks
between pages; a transfer has no such seam, so a server asked to stop leaves a
running transfer alone. That is safe rather than merely tolerable: content
becomes visible only once it is whole (ADR-012), so an abandoned transfer leaves
no half file in the library.

## ADR-027: Serving stored files from the same origin, safely

The library shows what it holds: a PDF, an image, a text file, a stored HTML
page. MaxiCrawler renders none of them. It states a content type, hands the bytes
over, and lets the browser do what browsers are good at — no PDF renderer, no
Markdown converter, no image decoder, and nothing to keep up to date.

That leaves one real problem. **Two of those types are executable code.** A
downloaded HTML page or SVG, served inline from `http://127.0.0.1:8000/`, runs in
*this application's origin*, and there is no authentication in front of it
(ADR-025). Such a page could read the settings page, start a crawl, start a
download — anything a person with the tab open could do.

The answer is `Content-Security-Policy: sandbox`, which makes the browser treat
the response as its own opaque origin, plus the `sandbox` attribute on the frame
that shows it. Verified rather than assumed: from the page around it, the framed
document is unreachable.

**It is applied to HTML and SVG only, and that split is a measurement.** The
first version sent the policy on every inline answer, on the reasonable ground
that "which types are dangerous" is a question answered wrongly once and then
kept. Chrome then refuses to render a PDF: `ERR_BLOCKED_BY_CLIENT`, because the
directive blocks the plugin its viewer is, and the frame attribute blocks it
again. A PDF, an image and plain text cannot execute script in our origin — a
PDF's own script runs inside the browser's viewer, not in the page that framed
it — so the policy would have cost the whole feature and bought nothing.

Four smaller decisions follow from the same reasoning:

- **An allow-list, never `mimetypes`.** That module reads the Windows registry:
  the type of a `.webp` differs between a developer's machine and the CI meant to
  check it, and this install has no entry for it at all. A content type decides
  whether a browser executes something, which makes "it depends on the machine"
  the wrong property for it to have.
- **SVG is an `<img>`, never a frame.** An image element runs no script even
  when the file behind it contains some.
- **Markdown is `text/plain`.** No browser renders Markdown, `text/markdown`
  makes Chrome download it, and converting it would mean rendering it ourselves.
  Showing the source is the only reading of "let the browser display it" that is
  also "do not convert it".
- **Downloading states no type at all.** `…/file` is always
  `application/octet-stream` and always an attachment, so no browser gets to
  decide to render what it receives. Only `…/view` names a type, and only after
  the table allowed it.

A path arrives in a URL, so it is not trusted. `Library.entry_at` accepts only
components this project could have minted, resolves the result, and refuses
anything that leaves the root — which a symbolic link inside the library would.
A file above `max_view_bytes` (32 MiB) is offered rather than shown, because a
browser handed a 400 MB text file stops answering.

There is no "open in the file manager" button, and there cannot be one that
works: a `file://` link from an `http://` page is blocked by every browser, and
having the server run `explorer` on an HTTP request would mean a web page
launching a local program. The path is shown in a field that selects on click,
with a copy button that appears only when scripting can make it work.

## ADR-028: Reading the library is its own service

`DownloadService` writes into the library; `LibraryService` reads it. Two
questions about one store, and keeping them apart is what stopped the first from
growing a second vocabulary — searching, sorting, paging and content types have
nothing to do with how a transfer is executed.

Both live in `maxicrawler.app`, so the browser and a future `library list`
command cannot disagree about what "sorted by name" means. That is the same
argument ADR-022 made for crawling and ADR-026 for downloading, applied a third
time; the web layer still imports neither `library` nor `downloader` nor
`providers`, and `tests/test_api_boundaries.py` reads the import graph to say so.

**The file system stays the index.** A query reads one small document per stored
resource and no database (ADR-010). The cost is measured rather than assumed: on
the machine this was written on, two thousand entries take about 0.3 seconds warm
and roughly sixteen the first time, while the virus scanner has its turn. Paging
does not help, because searching and sorting need every record. An index kept as
a *cache*, invalidated by modification time, would — and ADR-010 already permits
one on exactly those terms. It is not built, because a library of a few dozen
entries does not notice and a cache nobody needs goes stale.

Three properties of the listing are worth stating, because each is a wrong answer
if reversed:

- Records are read, then filtered, then sorted, then cut to a page. Sorting after
  paging would order a page instead of the library.
- Every ordering ends in the entry's own identity, so two files with the same name
  cannot swap places between two requests.
- A value nobody recorded sorts last in *either* direction. "Unknown" is not a
  small size, and a descending list would otherwise open with it.

A failed download is a row rather than a silence, because "where did my failed
download go" is exactly the question somebody brings to a library. It has no
payload, so it offers no file — and a record claiming a payload that is not on
disk says so, rather than offering bytes that are gone.

## ADR-029: robots.txt is obeyed by default, and parsed by Protego

Two decisions, taken together because neither is worth much alone.

**On by default.** ADR-016 argued the opposite, and its reasoning has expired
rather than been overruled: fetching *one page named by its operator* is what a
browser does when the same person types the same address. Since Sprint 9 a crawl
follows links, and since Sprint 11 the URL arrives from a browser rather than
from the person who started the process. Something that fetches many pages
unattended is a bot however it was started, and robots.txt is the convention
bots are held to.

The cost is real and worth naming. With `--depth 0` — still the default — a
crawl *is* one page a person named, and MaxiCrawler will now refuse where a
browser would not. The workflow this project exists for feels it most: forums
and link lists, exactly the pages share links sit on, are often disallowed
wholesale.

So the escape is deliberately cheap and visible: `--ignore-robots` on the
command line, a checkbox on the crawl form, `respect_robots` in the
configuration. A safe default nobody can find is a default people work around
instead of with. And it is never silent — a refused URL is counted under
`SkipReason.ROBOTS_TXT`, which exists precisely so that *"outside my scope"* and
*"the site said no"* cannot be read as the same sentence.

There is no exemption for the seed. "robots.txt applies, except at depth 0"
makes one rule depend on another option, and rules like that become bugs.

**robots.txt governs crawling only.** No provider consults it. A download is an
explicit act on a resource a person named, and file hosts disallow crawlers as a
matter of course; applying it there would break the second half of the chain to
no one's benefit.

**Protego rather than a parser of ours, and rather than the standard library.**
RFC 9309 requires wildcards, an end-of-match anchor, longest-match precedence
with `Allow` breaking ties, and group selection by product token.
`urllib.robotparser` predates all of it and compares paths with `startswith`, so
`Disallow: /*.pdf$` matches nothing there — we would fetch what a site forbade
while believing we obeyed. Under-obeying quietly is worse than not obeying at
all.

Protego was evaluated rather than assumed: 26 checks over the six behaviours we
depend on, of which 25 passed. It is BSD-3, pure Python with no dependencies of
its own, ships `py.typed` (so `mypy --strict` needs no override, unlike
`brotli`), and is maintained by the Scrapy project. It parses a string and opens
no socket, which a test asserts — fetching stays ours.

It is a *core* dependency rather than an extra, because `respect_robots`
defaults to true and a safe default that silently lapses when an extra is
missing is worse than a dependency.

The 26th check is ours to fix and is fixed: Protego does not strip a UTF-8
byte-order mark, so a robots.txt beginning with one loses its first group and a
file forbidding everything permits everything. RFC 9309 says the document is
UTF-8 whatever the server announced and a BOM must be ignored, so
`decode_robots` owns that. `tests/test_web_robots.py` states what would have to
stay true if the library were ever swapped.

Status handling is the RFC's, and is the one place a failure becomes a
permission:

| Answer | Treated as |
| --- | --- |
| 2xx | the rules it states |
| 4xx, including 401 and 403 | "unavailable" — no restrictions |
| unreadable: not text, too large, a chain that never resolved | no restrictions |
| 5xx, a timeout, a refused connection | "unreachable" — complete disallow |

The last is the only case where something other than a rule stops a crawl, which
is why `robots_deny_on_error` can invert it. Not knowing what a site permits is
not permission.

## ADR-030: Two gates, and the difference between them is cost

The engine had one gate, at the moment a URL was found. That is the right place
for a rule that answers from the URL itself and the wrong place for one that has
to make a request to answer.

Asking robots.txt there would tie the number of `/robots.txt` requests to the
number of *discovered* hosts rather than to anything the operator set. One page
linking to three hundred domains would cost three hundred requests before fifty
pages were crawled: the frontier is bounded by depth and the page ceiling, and
the set of hosts a page mentions is bounded by nobody.

So there are two, and one rule decides which a policy belongs to:

> A policy that can make a request is asked once, immediately before the request
> it guards. A policy that cannot is asked when the URL is found, so the
> frontier stays clean.

Both count every refusal through the same `skip_reason_for`, so there is still
exactly one vocabulary for "why not". A refusal at the second gate does not
spend the page ceiling: it never became a request, and the ceiling counts
requests.

The alternative — reusing the unused policy seam on `WebDiscoveryService` —
works, and was rejected because it makes skip counting a matter of catching
exceptions and splits the translation across two files.

Politeness is *not* a third gate. "May I fetch this?" and "may I fetch it yet?"
are different questions, and a policy that answered "not yet" would have to be
asked again in a loop that something would then have to own. Waiting belongs
where the request is made: `ThrottledFetcher` wraps a `PageFetcher`, the engine
still knows nothing about time, and there is no `sleep` above that file.

The politeness state is a separate object because of a loop that would otherwise
close: `RobotsPolicy` needs a fetcher to read robots.txt, and a throttle needs
`RobotsPolicy` to learn a host's `Crawl-delay`. Both fetchers share one
`HostSchedule`; the page fetcher asks robots for its delay and the robots fetcher
asks nobody, so its request is spaced like any other without the file having to
describe its own retrieval.

`crawl_delay` defaults to **0.0**. A host that wants to be crawled slowly says so
in its robots.txt and is obeyed up to `max_crawl_delay`; a delay nobody asked for
is a cost with no beneficiary. The clamp is not optional — one hostile line
saying `Crawl-delay: 86400` would otherwise freeze a crawl.

## ADR-031: The private-network guard, and what it cannot do

Until Sprint 11 the only person who could name a URL was the person running the
program. A web interface changes that: a URL arrives from a browser, and a
browser can be pointed at a form by any page it visits. `http://localhost:9200/`
stops being an odd thing to type and starts being a request that arrives on its
own.

All of it is a `CrawlPolicy`. Neither the engine nor the fetcher learns what an
internal address is.

Two checks, because they cost differently. The **literal** check reads the URL
and is pure, so it runs at the first gate and keeps a page full of links to this
machine out of the frontier. The **resolved** check asks the resolver, which is
what catches `metadata.google.internal`, an intranet name, and the services that
answer `127.0.0.1` for anything; it runs at the second gate.

Both had to agree with the socket rather than with `ipaddress`. `127.1`,
`0x7f.0.0.1` and `2130706433` are rejected by `ipaddress` as malformed and
accepted by the C resolver as loopback, so a guard trusting only the strict
reading would call them host names, resolve nothing, permit the fetch — and the
connection would go to loopback anyway. Both readings are tried.

**The redirect is where SSRF actually lives.** A public URL answering
`302 Location: http://169.254.169.254/` walks straight past a check made once at
the start, so the fetcher calls a guard on every hop. It takes a plain callable
that raises, so `maxicrawler.web.fetcher` still imports no policy.

Allowing private addresses does **not** allow a metadata service. Somebody who
opens their intranet to a crawler has not volunteered their cloud credentials,
and the two are one setting only by accident of both being "not the internet". A
named entry in `private_network_allowlist` still beats everything, which is what
makes crawling one machine on a home network possible without opening the rest.

**What this does not close: DNS rebinding.** Between our lookup and `urllib`'s
there is a second lookup, and a name that answers differently each time can pass
the first and be used by the second. Closing it means pinning the address we
checked onto the connection actually opened, which is a change to how sockets
are made rather than to a policy. This raises the cost of reaching an internal
address; it does not make it impossible, and saying so is worth more than a
guard that claims otherwise.

## ADR-032: A download is stopped in the sink

A crawl has had a stop button since Sprint 9: `CrawlControl` is an `Event` the
engine checks between pages. A transfer had no such seam, so "stop" meant "wait
for the file", and `serve` shutting down held on until it was done.

`DownloadControl` is the same object for the other half of the chain — two
background things in one server should not have two designs (ADR-024) — and
where it is checked is the whole decision.

Not in the manager: that is between jobs, and would only stop the *next* file.
Not in the provider: every provider would implement cancellation and one of them
would forget. It is checked in `LibrarySink.write`, before the chunk is written.
That is the one place every provider's bytes already pass through, and the place
that already guarantees an unfinished transfer leaves nothing behind (ADR-012).
So a cancelled download is not a special path: it raises where a broken
connection raises, the staging file is discarded exactly as it always was, and
the library is left as it was.

**A cancelled transfer writes no metadata record.** A record saying "failed"
would turn somebody's own decision into a fault they later have to explain, and
would count an attempt nobody made, which the next run would report as a retry.
`DownloadStatus.CANCELLED` therefore lives only in an outcome being shown right
now and never in a stored document — so nothing this release writes is
unreadable by an earlier one, which `_read_enum` would otherwise refuse.

Nowhere a person reads it is a stopped download spelled as a failure. They
pressed the button.

`DownloadControl` reaches `maxicrawler.api` through `maxicrawler.app`, not from
the download layer, because the interface may not import `downloader` (ADR-022)
and should not start now. It is a handle rather than a result: nothing about how
a download runs travels with it.

## ADR-033: A download queue, and what it deliberately does not promise

ADR-026 said "one at a time, and no queue", and gave the reason: a queue needs a
policy for ordering, cancelling, resuming and surviving a restart, and none of
that was worth inventing before a single download worked end to end. It has
worked for two sprints. This is the ADR that pays that debt, and it answers
three of those four questions and refuses the fourth.

**Ordering: the order they arrived, with a way to change it.** A queue that
guessed at priorities would need a reason to guess, and there is not one — the
person adding the links knows which matters. So the default is arrival order,
and moving one up, down or to the front is three form buttons. No drag and drop:
that is a JavaScript dependency for the last five percent of a control the
buttons already give, on a page that otherwise works with scripting off.

**Cancelling: one intention, two costs.** Removing a waiting request and
stopping a running one are the same click for the person doing it — this
download should not happen. They cost differently underneath: one never started,
the other stops within a chunk. Neither leaves anything in the library, because
content becomes visible only once it is whole (ADR-012). A removed request is
recorded as `CANCELLED` with a reason, not as a failure; the person reading the
word is the person who pressed the button.

**Pausing: the queue, not the transfer.** Pause stops the worker from taking
anything *new* off the queue and leaves the running transfer alone. "Let me
think" and "undo what is happening" are different intentions, and the running
download already has its own Stop. One button doing both would make the cheap,
reversible action carry the cost of the expensive one.

**Resuming a transfer: not this.** The word is overloaded and the overload is
dangerous, so it is worth stating plainly: this sprint resumes a *queue*, never
a *file*. Resuming a partial transfer needs HTTP range requests, a byte offset
in the metadata record, and a provider that supports both. It stays on the
roadmap where it was.

**Surviving a restart: not this either.** The queue lives in memory and ends
with the process, like the crawl-job registry beside it (ADR-024). Persisting it
is the Crawl Jobs subject, and it drags in a question this sprint cannot answer
honestly: what does a half-finished transfer come back as? Until "resume" exists,
a restored queue could only offer to start those files again from zero, which is
what the person can do themselves from the library.

**One worker, and that is a politeness decision.** The queue, the run and the
worker loop are all written for more than one — every mutation is guarded, the
worker holds no state between requests, and a second thread on the same drain
loop would need no other change. What stops it is that "how many transfers may
one host face at once" is the same kind of question `robots.txt` answers for
crawling, and this sprint is about a person's workflow rather than a host's
patience.

**`DownloadService` is still the only thing that starts a download.** The queue
decides which request is next and whether the worker may take it. Every transfer
that happens is one `DownloadService.download` call, unchanged, with the same
progress listener and the same control handle. There is no second download path,
and the bulk selection that follows adds none: it resolves a selection into URLs
and puts them in this queue.

**A ceiling instead of an unbounded backlog.** Five hundred waiting requests,
refused above that with a message naming the limit. One click on a filtered
report will soon be able to ask for every match at once, and a queue that
accepted forty thousand would be a memory problem wearing a convenience.

**The credential is now held longer, and confined harder.** A queued request
keeps its whole URL, fragment included, because the transfer that needs the
decryption key has not started yet — and a retry may need it again after that.
It lives in one private dictionary on the queue, is dropped when the run is
evicted, and reaches nothing else: `DownloadRun` still knows only the
fragment-free URL, which is what every snapshot, page, event frame and redirect
is built from. `tests/test_api_secret_confinement.py` reads that rather than
trusting it. The exposure is smaller than the longer life suggests: discovery
already writes the same URL, key included, into SQLite, and the report renders
it into a table — a share link *is* its key, and one without it leads nowhere.

**Two queues, named apart.** `maxicrawler.downloader.queue.DownloadQueue` holds
the jobs of one plan; `maxicrawler.api.downloads.TransferQueue` holds requests
nobody has planned yet. They are not merged because they answer different
questions and because `api` may not import `downloader` at all. They are not
*named* alike because `tests/test_api_boundaries.py` forbids `api` from naming
the download layer's builders and matches on the class name alone — two classes
called `DownloadQueue` would have made a real rule unenforceable to save one
word. The test found this rather than a review; that is what it is for.

## ADR-034: Queueing a set of links, and the two shapes it takes

Ticking a box beside two hundred links is not less work than clicking two
hundred buttons — it is the same work with an extra step at the end. So this
sprint's selection feature is two controls, and the second one is the reason
the first is worth having.

**Queue selected** takes the rows that were ticked. The URLs travel in the
request body, one field per row, because a share link keeps its decryption key
in the URL fragment and a fragment is the one part of a URL a browser never
sends in a link. In a field it survives; in an `href` it would be gone before
the server saw it.

**Queue every fetchable match** takes the filter instead. The query string of
the report travels in the form's action, the server re-runs it against what the
crawl recorded, and the URLs — keys and all — never leave this process. One
click replaces every checkbox on every page of a filtered report, which is the
control this sprint exists for: a filtered report is a set somebody has already
decided on, and ticking it again is asking them to say it twice.

The second is also the safer half, and that is not a coincidence. Sending a set
by *describing* it beats sending it by *enumerating* it whenever the elements
carry credentials.

**The checkboxes belong to a form they are not inside.** HTML forms cannot
nest, and every downloadable row already carries a form for its own Download
button. The batch form therefore sits beside the table and the checkboxes join
it by `id`, which is exactly what the HTML `form` attribute is for. No script
is involved: a browser submits an associated control as if it were nested. The
alternative was removing the per-row button, which would have made the common
case — one link, one click — worse to make the rare case possible.

**No "select all" checkbox.** It cannot be done without JavaScript, and the
control it would approximate already exists and is better: "every match" covers
every page rather than the two hundred rows currently rendered.

**Reordering by dragging is still out**, for the same reason it was in ADR-033.
Three buttons on the queue page do it, they work by keyboard, and they need no
build step.

**A batch is partial, not atomic.** Two hundred links where three are malformed
and the queue has room for a hundred and fifty is a job mostly done, not an
error. `submit_all` returns three numbers — queued, rejected, no room — because
those need three different sentences: a malformed link is something to fix, a
full queue is something to wait for, and neither is a reason to have refused
the ones that were fine. Only a batch that queued *nothing* is answered with a
refusal page.

**Where you land afterwards is decided by what you asked for.** One link goes
to that download's page, because watching it is why somebody queued one. Several
go to the queue, because that is the thing they just changed.

**The ceiling is asked about before the work, not after.** `TransferQueue.room`
exists so that resolving a filter into four hundred URLs and then refusing them
one at a time is not how somebody learns the queue is full. What does not fit is
counted and reported, never dropped quietly.

**`DiscoveryService.fetchable` is a query, not a page.** It could have been
`browse` with a page size nobody would want rendered, and that would have
conflated two questions: "which rows do I show" carries ordering, facets, paging
and column choices, while "which URLs do I queue" carries none of them. Same
service, same filter vocabulary, different answer — which is the shape ADR-028
set up when it separated reading the library from writing it.
