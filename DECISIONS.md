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
