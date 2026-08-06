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
