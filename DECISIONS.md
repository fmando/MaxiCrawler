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
no TypeScript; the browser loads one stylesheet and a handful of small scripts,
all written by hand and served out of the package.

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

ADR-038 revisits both paragraphs. A script replaces a region of the queue page
now rather than only numbers inside it, which needs the rule stated as what a
script may *not* do rather than as how little it does — and the htmx accounting
is worth redoing once the cheap half has actually been paid for.

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
worked since. This is the ADR that pays that debt, and it answers
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

## ADR-035: A scope that names a place, not just a host

A crawl could be held to a domain or to nothing. That is the wrong number of
choices for the sites people actually point this at.

`boards.example.org/hr/` and `boards.example.org/g/` are one host. So are
`example.org/~alice/` and `example.org/~bob/`, and every version of a
documentation set under `/docs/v1/` and `/docs/v2/`. On all of them
`--same-domain` is a rule that ticks a box and changes nothing: the crawl walks
the whole site because the whole site *is* the same domain, and the operator
filters thousands of unwanted links afterwards instead of never recording them.

`--below-seed` is the third scope: the place the start URL names, and anything
under it.

**A path prefix carries its host, so it replaces the domain rule rather than
joining it.** A rule that matched `/hr/` on any host would hand the crawl to
every site with a section of that name. Two boxes ticked is therefore not a
contradiction to resolve — it is the narrow rule, and the wide one beside it
could only ever agree.

**Subdomains are always outside it**, with no option to include them.
`docs.example.org/guide/` is not a place below `example.org/guide/`; it is a
different site whose address looks similar. Where the domain rule offers the
choice, this one does not, because there is no reading of "below this URL" that
reaches another host.

**Matching is by whole path segment.** `/hr/` must not admit `/hrx/`, which is
the same hole a suffix test opens in a same-domain rule, and it is asserted by
name in the tests. `/hr` and `/hr/` are the same place, whichever of them a
link happens to be written as.

**One guess, written down: a last segment with a dot in it is read as a file.**
`https://example.org/docs/guide.html` covers `/docs/`, not nothing. Without
that, the option would confine a crawl to a single page on most of the URLs
people paste — which is precisely when they reached for it. The guess is wrong
for `/releases/v1.0`, where it widens the scope to `/releases/`; a trailing
slash settles the question in the other direction, and there is a test whose
name says the rule is wrong there on purpose.

**Three booleans, one scope.** `same_domain`, `include_subdomains` and
`below_seed` can be spelled eight ways and mean four things. `CrawlOptions.scope`
decides the precedence once and returns a `CrawlScope` whose values *are* the
phrase, the way `PolicyRule`'s are. The engine picks its policy from it, the
crawl table renders it, the terminal prints it, and the JSON document carries it
beside the raw booleans. Before this there were two places that worked the
precedence out — a view and a CLI renderer — and a third about to be added.

**A record keeps what it was told, not what it should have been told.** The
database stores all three flags rather than collapsing them on the way in. A
run submitted with both boxes ticked did what `below_seed` says; the row still
shows both, and `scope` is what says which one governed. Rewriting the row to
match the rule that won would destroy the only evidence of what was asked for.

**Scope decides what is fetched, not what is recorded.** Every link on a page
that *was* fetched still reaches the discovery pipeline and still appears in the
report, including the ones pointing out of scope — that is deliberate and
predates this decision (a Mega link out of scope is still classified). What the
narrow scope removes is the pages that would have been fetched *for* their
links, which is where the volume actually comes from.

## ADR-036: A provider for files that are simply at a URL

Until now MaxiCrawler could classify an image, count it, sort it, filter it and
show it in a report — and not fetch it. `DownloadService.downloadable` answers
yes only where a plugin classifies a URL *and* a provider claims that
classification *and* the provider was composed for transfer, and the only
provider was Mega. Every ordinary file on the web fell through the last two.

`DirectProvider` claims what nothing else does.

**It claims every HTTP(S) URL, a page included, and that is the honest
answer.** It really can transfer any of them. The consequence is that *"could
this be downloaded?"* stops being a discriminating question — which is a fact
about reporting rather than about the provider, and is dealt with where reports
are made: the filter is withdrawn where it separates nothing, and `TargetKind`,
which reads the URL's own suffix, is what tells an image from a page.

**Lowest priority, for the reason the generic plugin has it.** A Mega link must
reach the provider that can decrypt it, not the one that would faithfully store
its ciphertext. A registry resolves by descending priority and stops at the
first claim, so ordering is the whole of the arrangement — no provider had to
learn about another.

**No listing, ever.** A URL names one file; a page that lists more of them is a
*crawl*, and there is one of those already.
`ProviderCapability.LIST` is not advertised and `entries` is always empty.
Enumerating a directory index behind a provider would be a second crawler
wearing a different hat.

### The guard had to move first

This is the first provider that fetches what a *crawl* found rather than what a
host's API returned, which puts the SSRF surface of ADR-031 in front of
`providers` for the first time. `providers` cannot import `web`, so the rule
had to move or be written twice — and two definitions of "internal" that drift
apart is a hole neither file shows.

What moved is the **judgement**, not just the vocabulary.
`utils.addresses.PrivateNetworkRule` answers with a *sentence or nothing*: the
reason a host is refused, in words, knowing no decision type and no exception
type. Those belong to whoever asked. `web.private.PrivateNetworkPolicy` turns
the sentence into a `PolicyDecision` a crawl records as a skip; the file
transport turns the same sentence into an `AddressRefusedError`. One rule, two
vocabularies, and a test asserts that the policy's reason *is* the rule's
sentence.

**Refusing is not the transport's caller's option.** `UrllibFileTransport`
built without a rule builds the strict one. A transport somebody wired without
thinking about it is the safe transport rather than the open one, and reaching
a home network stays possible by handing in a rule that says so — a decision
somebody makes rather than one they omit.

### Three smaller decisions

**A stated filename is reported unsanitized.** `Content-Disposition` says what
a host wants a file called; `library.naming.safe_filename` already cleans every
name the library stores. Two sanitizers on one string is one too many, and the
test asserts the pairing rather than adding a second. A URL's last path segment
is a *guess* about a name, so it is the provider's, not the transport's: a
`RemoteFile` describes what came back and nothing else.

**`head` returns a refusing status; `open` raises it.** 404 describes a
resource, and an inspection has somewhere to put it — `Availability.NOT_FOUND`.
A transfer has no content to hand back and no partial answer worth giving. Only
the statuses that say something about the *resource* are mapped; every 5xx is
`UNKNOWN` rather than a guess, because a server that is broken has not said the
file is gone.

**Identity splits across the host and the path.** A library key is a readable
slug of `resource_id` beside a digest of the whole identity, so the path in
`resource_id` makes an entry `ls` can be read on — `img5678png-f3b390ddda`
rather than `httpsi4cdnorg…` — and keeping the host in the identity is what
stops `a.test/1.jpg` and `b.test/1.jpg` becoming one entry.

### What is not decided here

**No size ceiling.** `UrllibStreamTransport` deliberately has none and this
inherits that: a transfer is expected to be large and is written straight to
disk rather than accumulated. It is worth writing down rather than assuming,
because this is the first provider that makes it possible to fetch very much by
accident — one filter, one click. What bounds a run today is the queue's own
limit, and a byte ceiling would be a setting rather than a rule.

**robots.txt still does not apply to downloads**, and never did: a download is
an explicit act on a named resource. That was of no consequence while the only
reachable host was Mega. It is of consequence now — this can take a site's image directory
in one go — so it is restated here rather than quietly relied on. The decision
stands; what changed is that it is worth seeing.

**`direct_downloads` is not a safety setting.** It answers *"does this
installation fetch arbitrary files at all?"*, which an installation is entitled
to decide in one place, and it withholds the transport rather than removing the
provider so a registry keeps its shape. What keeps a download off this machine
and this network is the private-network rule, which applies either way.

## ADR-037: The library index is a cache, and only set questions may use it

ADR-010 made the file system the authority and said an index might follow as a
cache. It has, and the reason is a measurement rather than a preference: every
listing read one metadata document per stored resource — about 0.3 seconds for
two thousand entries warm, and roughly sixteen the first time a virus scanner
saw them.

`SQLiteLibraryIndex` is that cache. What keeps it from quietly becoming a second
library is two rules, and both are enforced above it rather than inside it.

**Only *set* questions may be answered from it.** A listing, and "is this URL
among them?" — which is what the report's *in library* mark asks. A single entry
is still read from its own directory. That is the whole of the safety argument:
a stale row can delay a listing by one refresh and can never serve the wrong
file, because nothing ever hands back a file on the strength of a row.

**A row is trusted only while the document it was read from has the same
modification time and size.** Every entry is `stat`-ed on every listing; only
the documents that changed are parsed again. So the index is not a claim about
what the library holds, it is a memory of what was read last time — and a
library edited with a text editor, restored from a backup or repaired by hand
corrects it on the next listing without anybody being told to rebuild anything.

**The verbatim document is stored beside the extracted columns.** Extracting
every member would mean the adapter knowing what a metadata document contains,
which is the library's business and changes when it does (ADR-013). A document
kept whole survives a release that adds a member, because the layer that
understands it is the layer that reads it back.

### `entry_id` exists and is deliberately empty

The column is reserved so that writing an identity later is a write rather than
a migration — `CREATE TABLE IF NOT EXISTS` does nothing at all to a table that
already exists, so a column added after a release needs the appended-column
handling `maxicrawler.database.crawls` shows.

It is not written, and that was decided rather than deferred. A library entry
already has a stable identity: `resource_key` is derived from the reference
alone, never from a name, a size or a timestamp, so an entry keeps its place
across renames, re-inspections and restarts. What a separate identity would add
is *independence from the address* — one file reached through two share links, a
provider that changes how it composes a resource id — and that is a feature
nobody has designed here yet.

A **random** identity would buy that independence at a price this design cannot
pay: it would live only inside the metadata document, and it is therefore the
one piece of state in the library that cannot be recomputed from the file
system. A library restored from a backup would come back with different ids, and
everything pointing at the old ones would dangle. That is exactly the property
ADR-010 exists to prevent. It would also need a read before every write, because
`DownloadManager` rebuilds the record on each status change — and an id that is
re-minted whenever that read fails was not stable in the first place.

A **derived** identity costs none of that and buys none of the independence
either: derived from the same reference, it is a second name for the identity
`resource_key` already is.

The case that motivates the column is duplicates, and the natural anchor for
duplicates is the **checksum** — which is address-independent *and* recomputable
from the file, so it is ADR-010-shaped where a random id is not. `ContentRecord`
records checksums already and the index has a column for one. So the column
stays empty until the question that needs it is asked, and that question gets to
choose the answer.

## ADR-038: JavaScript is an extra, and what it is not allowed to decide

ADR-023 said the interface is complete without JavaScript and that a script only
saves you reloading. That held while there was one script writing numbers into a
page. There are four now, and one of them replaces a whole region of the queue
page, so "an extra" needs a sharper edge than a statement about how much code
there is.

**The rule is that a script decides nothing and formats nothing.**

*Formats nothing*: every value written into a page was formatted by the same
server code that rendered the page. `stream.download_payload` is literally
`views.download_view` — the event frame carries "1 min 23 s" rather than 83,
precisely so that there is no second implementation of that phrase living in
JavaScript and free to disagree with the one a reload produces. The one number a
script composes itself is the count in `select.js`, and it is admissible because
the page size is fixed at two hundred: it can never reach the width at which the
rest of the interface would group digits and this would not.

*Decides nothing*: where to look next is written by the server into a data
attribute. `download.js` reads three of them — which stream to listen to, where
to ask when that stream ends, and where the answer goes — and the difference
between the queue page and one download's page is which of them the server
renders. The script does not know there are two pages.

**A control that would not work is not rendered as if it would.** `copy.js` and
`select.js` render their controls hidden and reveal them; a Copy button that
copies nothing and a header checkbox that ticks nothing are worse than neither.

**And nothing is reachable only through a script.** Every batch is a form, every
reorder is a form, every fold is a link. What the scripts remove is two hundred
clicks and two hundred page loads, not the two hundredth way of doing it.

### Why still not htmx

Its licence (0BSD) was checked and it was considered again here, because a
fragment swap is exactly what it is for. The accounting came out the same way.

- The expensive half of adopting it — routes that render standalone fragments —
  was already done and remains done. `/downloads?part=queue` answers with the
  same partial the page includes.
- The cheap half cost about forty lines and no dependency, and those forty lines
  hold something htmx would not have given for free: the three-state answer to
  *what is left to follow*, where a missing stream between two transfers means
  "ask again" rather than "stop".
- Its server-sent-events support has already broken once between major versions
  and now lives in a separately released repository, while `EventSource` is a
  browser standard that will behave the same in ten years.

The point at which this is worth reopening is filtering and sorting the crawl
list, where the number of places that would each need their own small script
starts to exceed the number of kilobytes htmx costs.

## ADR-039: The way back from a batch

Queueing a set from a report used to answer with the queue. That is the right
answer to *"what did I just start?"* and the wrong one to what the person is
actually doing, which is working through a filtered report — and losing the
filter, the sort, the page and the columns in order to be told that forty things
were queued is a worse trade than not being told at all.

So a batch comes back to where it was queued from. The two controls arrive at
that differently, and the asymmetry is the decision.

**"Queue every fetchable match" rebuilds the way back on the server.** The
filter it acts on is already in the form's action, and the filter that says
*what* to queue is the same filter that says *which report* to return to. Two
copies of one filter are two things that can disagree, and the copy a browser
holds is the one that could be made to point somewhere else.

**"Queue selected" is told, because it cannot be asked.** It posts a set of
ticked URLs with no query string of its own, so the form carries a `back` field
— and everything that comes back from a browser goes through `_our_path`, which
accepts a path of ours and nothing else. A leading `//` is rejected along with
everything that is not a path, because a browser reads `//elsewhere.test/` as
another host, which is the one way a "go back afterwards" parameter turns into
an open redirect.

**The ticks do not come back, and are deliberately not restored.** The only way
to carry them would be to put the URLs in a query string, which is the one place
a share link's key must never go (ADR-020). What comes back instead is the rows
saying *in queue* — the same information without the credential, and true of the
ones somebody else queued too.

**The confirmation lives in the URL and lasts exactly one page.** What a batch
did is written into the redirect rather than kept in a session, because a
redirect is the whole of what this server remembers between two requests — and
because a confirmation that survived a reload would outlive being true. The
parameters that carry it are named in `views.TRANSIENT_PARAMS` and dropped by
the same function that carries every other parameter forward, so nothing has to
remember that it has already shown one.

## ADR-040: A verdict is not a transfer

What somebody thinks of a stored file is recorded **in that entry's own metadata
document**, as an optional `review` member holding a verdict, a favourite
switch, and the two times that belong to them. Not in the database: an index is
a cache that may be deleted and rebuilt (ADR-037), and a judgement that a
rebuild loses is not a judgement. The file system stays the authority (ADR-010),
so a library moved with `rsync` arrives with everything anybody decided about it.

**The schema number stays 1.** ADR-013 has a reader refuse a document whose
schema is higher than its own, so raising it would make every library written by
this release unreadable to the release before it — for an *added optional*
member, which is precisely what `extra` was built to survive. An older version
reads such a document, keeps `review` in `extra` untouched, and writes it back
unchanged. The number describes the format, and the format did not change.

**Two writers, disjoint fields.** ADR-028 kept reading and writing apart;
judging is writing, and it is the second writer:

- `DownloadWorker` rebuilds every transfer field and carries `review` **and**
  `extra` across from the previous record. Until this release it carried neither,
  which was invisible while nothing wrote them and would have quietly deleted a
  verdict on the first re-download.
- `LibraryService.review` rebuilds `review` and carries everything else across,
  read-modify-write through `LibraryEntry.read`/`.write`.

Nothing locks the directory, so a download finishing at the same moment as a
judgement can still lose one of them. That is named rather than solved: because
the two writers touch different members, the worst case is one judgement lost,
never a document describing a file that is not there.

`ReviewVerdict` is its own enum beside `DownloadStatus` for the same reason.
A status says how a transfer ended; a verdict says what somebody made of the
result. A file can arrive perfectly and be worthless, and one vocabulary for
both would have to answer *"did this download work?"* with *"they did not like
it"*. The queue's state is a third axis and lives in memory, so the query string
carries `status=`, `verdict=` and `state=queued` separately and never merges them.

**Auto-advance decides the successor before it writes.** Judging a file from a
listing sends the browser to the next file in *that* listing, and the listing is
the point: with the *unreviewed* filter on, the row being judged leaves the set
the moment the verdict lands, so a successor looked up afterwards is already one
file too far — every click would skip one. The listing travels in its own `walk`
parameter rather than in `back`, because the two answer different questions:
`back` is where a control that does *not* advance returns to (the file's own
page, ADR-039), and `walk` is the set the next file comes from. Both go through
`_our_path`. Only the three verdicts advance; taking one back and starring stay
on the file, because a correction belongs on the thing it corrects.

## ADR-041: Discarding removes the file and keeps the headstone

*Ignore* and *discard* are two decisions, not one with a stronger adverb.
Ignoring means *do not show me this again* and leaves the file alone. Discarding
means *and take the bytes back*, and it is the only thing in the interface that
deletes.

**One call does both halves**, `LibraryService.discard`, and the two halves have
an order. The file goes first, the document second. Deleting the payload without
writing the headstone leaves an entry the next bulk queue cheerfully fetches
again; writing the headstone without deleting the payload leaves a record
claiming a file is gone while it sits on disk, which everything downstream reads
as *do not fetch this again* — the file would still be there and no longer
reachable. In this order the failure that actually happens, a file something
else holds open, leaves the entry untouched. `review()` refuses the discarded
verdict outright for the same reason: `discard()` is its only writer, and it
removes the file first.

**What stays is everything the record said about the payload** — its name, its
size, its checksum. That is what makes *"show me what I threw away"* a view
rather than an excavation, and it is what makes the headstone work at all: "the
library holds this" is answered by the record *and* the file, and only the file
is gone.

**The promise is kept in three places, because two would make it a lie:**

1. `DownloadWorker.execute` refuses a dismissed record — after asking whether the
   payload is already stored, so *"the library already holds it"* stays the
   answer when it is the more useful one, and it writes nothing at all. A record
   rebuilt to say "refused" would lose the status and the content the entry
   already had.
2. A report marks such a link *dismissed*, through one more `LinkState` member
   and one more resolver in the same mapping the *in library* and *in queue*
   marks already come through.
3. *"Queue every fetchable match"* leaves them out, so they do not enter the
   queue only to be turned away at the far end, where a refusal reads as a fault.

**A URL counts as dismissed only when every entry recorded under it is.** Mega
gives each child of a folder the folder's own URL, so *any* would put a folder of
two hundred files out of reach because of one dismissed thumbnail. Queueing the
container stays right while a single file in it is wanted, and the worker still
turns away the individual entries, where the question has no ambiguity.

**There is no `restore()`.** Taking a discard back is `review()` with
*unreviewed*, the same call that takes any other verdict back — a second method
would be one operation under two names. What it cannot do is bring the file
back; what it does is lift the headstone, so downloading the link again restores
it, which is the safety net ADR-012 already promised. The removal time belongs to
the verdict and is cleared in the same write, because a deletion time that
outlived the verdict would ride along on the next download and describe a file
that is there.

## ADR-042: A floor under what is worth downloading

An image directory answers with a thumbnail, a sprite and an icon for every
picture in it. Clearing those out by hand is undone by the next *"queue every
match"*, so `min_download_size` (default 100 000 bytes, 0 turns it off) refuses
what is too small to be the thing anybody wanted.

**It lives in `DownloadSink`**, not in the crawler and not in a provider. The
sink is where every provider's bytes pass anyway, it is where a transfer is
already stopped (ADR-032), and it is the one place that is already guaranteed to
leave nothing behind. A rule in the providers would be a rule per provider.

**Two checkpoints, because a size is not always known in advance.** At `begin`,
a descriptor that states a size below the floor is refused before a byte is
transferred. At `commit`, a descriptor that stated no size at all is caught by
what actually arrived — and the file is still staged under `.incomplete/` at that
moment, so it is discarded without ever appearing in `content/`, which is the
mechanic ADR-012 already provides.

**It applies to an explicitly requested single download too.** One rule in one
place: the sink does not know who commissioned it, and two rules would need two
explanations. Somebody who wants a small file lowers the setting.

**A refusal writes a record.** Without one the decision is gone after a restart
and the next bulk queue fetches the same file again — the same trap the
headstone in ADR-041 exists for. It shows up as `SKIPPED` with the reason naming
both sizes, filterable in the library and readable in the queue's history. The
known objection stands and is not argued away: 100 000 bytes also catches small
PDFs and text files. What makes that bearable is that every refusal is visible
with its reason, and that the number lives in exactly one setting.

## ADR-043: A form of ours, or none at all

The interface has no authentication and says so (ADR-025): whoever reaches the
port can use it. That was a bounded statement while the worst a stray request
could do was start a crawl. It stopped being bounded when a button began
deleting files — any page in any other tab can submit a form at this server and
the browser will send it, needing no access to anything this server returns.

So every unsafe method must have come from a page of ours, decided in this order
by `SameOriginMiddleware`:

- **`Sec-Fetch-Site`**, which every current browser sends and no script can set —
  the property a token in a hidden field has to be given by hand. `same-origin`
  is ours; `none` is a user-initiated navigation and is accepted, because
  refusing it could only cost a legitimate request; `same-site` and `cross-site`
  are refused, the first because this server has no sibling hosts that ought to
  be posting to it.
- **`Origin`**, for a client that sent no `Sec-Fetch-*` header, compared against
  the `Host` the request was addressed to — behind a reverse proxy the scheme is
  not knowable and the host is.
- **Neither header: allowed**, and that is a decision. A browser always sends one
  of them cross-origin; what sends neither is `curl`, a script, a test —
  something already on the machine, which is the position the command line is in
  anyway. Refusing those would break every non-browser client to stop an attacker
  who could equally well send no header.

No token, no session, no cookie, and nothing to render, so every form keeps
working without JavaScript (ADR-023).

It is **pure ASGI rather than `BaseHTTPMiddleware`**, and that is load-bearing:
two routes answer with an event stream that stays open for the length of a
crawl, and `BaseHTTPMiddleware` buffers a response through a queue. This one
inspects the request and then either steps out of the way entirely or answers
instead of the application.

**It is not authentication and does not become any.** It stops a page somebody
else wrote from acting through a browser that can reach this server; it stops
nobody who can make a request directly. That stays the job of a reverse proxy in
front, and binding anywhere but loopback stays a deliberate act.

## ADR-044: A thumbnail is only ever a cache

0.16 drew a tile from the stored image below `preview_inline_bytes` and from a
symbol above it, and said so plainly: honest, and the reason a directory of
photographs showed mostly symbols. The measurement the plan asked for was taken
against a real library of 22,692 entries and settles it.

**The byte limit measures the wrong quantity.** A file's size says what is sent;
a browser then holds the decoded image at four bytes a pixel, whatever it was
compressed to. Of that library's images only 2% are under a megapixel, and 27%
of the ones the byte limit still lets through are over four. The sixty largest
of those come to **3.3 GB of bitmap on one page**, against the 1.5 GB the plan
set as the criterion. Meanwhile 47% of its images sit above the limit and had no
preview at all. Both halves are fixed by the same thing, and neither is fixable
by moving the limit.

**A thumbnail is a cache and nothing else**, which decides everything below:

- It can be deleted in full at any moment and the library is unchanged. Losing
  the directory costs one run of the maker.
- It never lives inside `library/`. A library directory holds what was
  downloaded and what the download said about itself; a picture MaxiCrawler drew
  is neither. The cache sits beside the metadata database, the other derived
  thing on the disk.
- It is never a statement an entry makes about itself. Nothing in the maker
  writes to a record.
- It is addressed by content — by the checksum the record already carries — so
  two entries holding the same picture share one, and a re-download that fetched
  identical bytes finds it already made. Without a checksum the entry's name and
  the file's modification time and length stand in, the same pair the listing
  cache trusts a row on (ADR-037).

**A tile prefers the thumbnail whenever there is one**, not only above some
size. That is the point: the images that need it most look small.

**The route serves and never makes.** A page of sixty tiles would otherwise be
sixty image decodes inside one request, and whoever opened a fresh library first
would pay for all of them. `scripts/make_thumbnails.py` is where the cost goes —
measured at about forty photographs a second, so a few thousand images is a
couple of minutes once and seconds afterwards. Until it has run, a tile shows
what it showed before, which is why nothing about this is a migration.

**Pillow is optional and its absence is an ordinary answer** — the same "no
thumbnail" a caller already handles for a file that is not an image. It is the
first dependency here that decodes untrusted bytes, and that is the cost being
accepted rather than a detail: a few kilobytes of header can claim dimensions
whose bitmap is tens of gigabytes. So there is one pixel ceiling rather than
two (Pillow's own sits lower and fires earlier, and is set to ours on import),
a half-written thumbnail is removed rather than left, and none of it runs in a
request path.

**Only images.** Video would need ffmpeg and PDF a renderer; both are their own
decision, and neither is made here.

