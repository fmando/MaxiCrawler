# MaxiCrawler Roadmap

## Vision

Build a modular, plugin-driven link discovery and download platform.

The mission, core principles, and non-goals are described in
[VISION.md](VISION.md).

## Milestones

-   0.1 Foundation ✅
-   0.2 Domain Model & Discovery ✅
-   0.3 Plugin API ✅
-   0.4 Discovery Engine ✅
-   0.5 HTTP Engine ✅ — transport, retries, metadata inspection, and
    streaming transfers
-   0.6 First Host Plugins ✅ — Mega is classified by a plugin, inspected and
    downloaded by a provider
-   0.7 Download Manager & Library ✅ — provider-independent downloads into a
    long-lived, self-describing store
-   0.8 Web Discovery ✅ — one page is fetched, parsed, and fed to the existing
    discovery pipeline
-   0.9 Recursive Crawling ✅ — a frontier, a visited set and a crawl engine
    above the single-page crawler
-   0.10 Web interface ✅ — a browser client of the same services the command
    line uses, served by `maxicrawler serve`
-   0.11 The first end-to-end workflow ✅ — crawl, report, download, library,
    in a browser, through a `DownloadService` both clients share
-   0.12 Library comfort & document viewer ✅ — search, filter, sort, paging, a
    page per file, and the browser showing what it can
-   0.13 Politeness & robots.txt ✅ — robots.txt obeyed by default, per-host
    waiting, a private-network guard, and a stoppable download
-   0.14 Workflow & productivity ✅ — a report you can search, filter and page
    through, a download queue you can reorder and pause, and one click to queue
    everything a filter matches
-   0.15 The report as a workspace ✅ — what is already known about each link,
    a way back from a batch that keeps the filter you queued it from, and a
    queue you can watch a hundred files through without reloading a page
-   0.16 The library as a workspace ✅ — tiles you can judge a file from, four
    verdicts that survive the next download, a discard that takes the bytes back
    and is not offered again, and a viewer you can walk a filtered listing
    through. Thumbnails followed once the measurement 0.16 asked for was taken
    on a real library: a tile of photographs was 3.3 GB of bitmap a page, and
    the byte limit could not fix it because it measures what is sent rather than
    what a browser holds (ADR-044)
-   0.17 Scheduler & Automation
-   0.18 REST API
-   1.0 Stable Release

The desktop GUI that used to sit at 0.11 is superseded by 0.10. A local server
and a browser reach every machine this runs on, including the ones it is run on
over SSH, and one interface that is maintained beats two that are not. The
`gui` package stays an empty placeholder rather than a promise.

## The chain

```text
Website → Crawler → Discovery → Plugin → Provider → Download Manager → Library
```

Each station answers exactly one question:

-   **Crawl Engine** — *"Which page comes next?"* Owns the frontier, the
    visited set and the limits; fetches nothing itself.
-   **Crawler** — *"Which URLs does this page contain?"* Fetches one page over
    HTTP; knows no provider, no download, and no library.
-   **Discovery** — *"Which URLs exist?"* Normalizes, deduplicates, and
    classifies whatever it is given, from a local document or from a page.
-   **Plugin** — *"Can I classify this URL?"* Pure, offline, runs on every
    discovered URL.
-   **Provider** — *"What can I do with this resource?"* May contact the host;
    invoked only by commands that perform network access.
-   **Download Manager** — *"How are downloads executed?"* Knows no provider.
-   **Library** — *"How are resources stored and managed?"* Knows no provider
    beyond its name.

Both clients walk the chain through `maxicrawler.app`: `CrawlService` for the
first half, `DownloadService` for the second, and `LibraryService` for reading
back what the chain produced.

Adding a host means adding a plugin and a provider. The last two stations do
not change.

## Next

-   Pinning the resolved address onto the connection that is opened, which is
    what closes DNS rebinding. The private-network guard raises the cost of
    reaching an internal address and does not make it impossible (ADR-031), and
    the difference is a custom `HTTPConnection` rather than another policy. Now
    worth more than it was: the rule guards downloads as well as crawls since
    ADR-036, so one change would close it in both places
-   A byte ceiling for one download, or for a queue's worth of them. Nothing
    bounds how much a transfer may be, which was academic while the only
    reachable host was Mega and is not now that one filter and one click can
    queue a site's image directory. A setting rather than a rule — the queue's
    own limit is what bounds a run today. The shape is settled since 0.16: the
    floor at the other end is `min_download_size`, checked in the sink at both
    the moment a size is announced and the moment the last byte lands (ADR-042),
    and a ceiling is the same two checkpoints with the comparison turned around
-   Making a thumbnail as soon as the file lands, so the maker stops being
    something to remember. Nothing produces one inside a request and nothing
    should (ADR-044) — but a download worker is already in the background with
    the finished file in its hand, which is a different place entirely. Two
    findings shape the work and are why it is more than the afternoon it looks
    like: `DownloadSummary` names a directory and a key only when the link
    turned out to be a *single* resource, so the case that matters — a share
    holding two hundred images — needs it to carry the whole list; and the
    decoding wants one thread of its own rather than the five transfer workers,
    which would otherwise hold five bitmaps of a large photograph at once. Until
    then a cron entry on the server runs `make_thumbnails.py`, which is what the
    maintenance page prints the command for
-   Parallel thumbnail making. The run is sequential and manages about forty
    photographs a second, which is a couple of minutes for a few thousand
    images and fine as a one-off; a library an order of magnitude larger would
    want the decoding spread over cores. Waits for somebody to have that library
-   Columns for what a listing filters and sorts by, so the index stops handing
    back a document to parse. The saving today is the file read, not the parse:
    two thousand rows come back in one database read and are parsed one JSON
    string at a time. The columns a judgement needs exist and already answer
    *stored* and *dismissed* without parsing; the rest waits for a measurement
    that says it is worth the extraction rules
-   ~~Deduplicating what is queued.~~ Done (ADR-046): the queue holds a URL
    once, waiting or running, and answers a second submission with the run that
    is already there. What is left of this subject is the *library* question
    rather than the queue's — one file reached through two different share
    links, which is the duplicates entry further down
-   A politeness schedule shared across concurrent crawls. Today a `HostSchedule`
    lives per crawl, which is exactly right while `serve` runs one worker and
    wrong the moment it runs two
-   Sitemaps, which robots.txt already tells us about and nothing yet reads
-   Real schema versioning for the SQLite metadata database. Today each adapter
    creates its tables with `CREATE TABLE IF NOT EXISTS` and declares the
    columns it has added since, which covers an appended column and nothing
    else. Renaming a column, changing a type, or backfilling a value needs a
    `user_version` and an ordered list of migrations — the same discipline
    `library.json` already has (ADR-013)
-   Per-page persistence — one `save_page` member on `CrawlRepository`, one
    call in the engine loop, and one table; `PageOutcome` already exists
-   A priority frontier, and a persistent one for resumable crawls — both are
    the same three-method protocol
-   Making `DiscoveryPipeline` thread-safe, which parallel crawling needs
    before a frontier can be drained by more than one worker
-   Crawl jobs — the unit the web interface manages, holding a `CrawlSession`
    beside its discovery results and its downloads. Today's registry is memory
    only: after a restart the pages fall back to what the database holds, and
    a crawl that was running is shown as abandoned. The download queue has the
    same shape and the same gap, and it cannot close it before resume exists —
    a restored queue could otherwise only offer to start the same files again
    from zero (ADR-033)
-   Duplicates: one file reached through two share links, or through a link
    whose address has changed. This is the case a separate identity for a
    library entry was reserved for, and 0.15 decided *not* to mint one yet
    (ADR-037): a random id cannot be recomputed from the file system, which is
    the one property ADR-010 exists to keep, and a derived one is a second name
    for the key an entry already has. The natural anchor is the checksum, which
    is address-independent and recomputable both — so the column stays empty
    until this question is asked properly, and this question chooses the answer
-   `library` commands — list, verify, prune, and now judge. `LibraryService`
    answers listing, verifying, reviewing and discarding already; what is
    missing is the command that asks them, and it is also the client that would
    show the browser is not the only way to reach a verdict. Pruning is still
    nobody's question: discarding takes the payload and deliberately keeps the
    record, so removing an entry outright is a different decision. Less pressing
    since the maintenance scripts arrived and the interface began printing the
    line that runs each of them (ADR-045): the jobs that were actually being
    reached for are done, from a shell, by whoever administers the machine —
    which is the argument for leaving them out of a public interface rather
    than for promoting them into one
-   One writer at a time per entry. A download finishing while somebody judges
    the same file can lose one of the two writes: the read and the write are
    consecutive and nothing locks the directory. The damage is bounded by the
    two writers touching disjoint members (ADR-040), so the worst case is one
    judgement lost rather than a document describing a file that is not there —
    which is why this is named rather than urgent. Bounded a second way since
    ADR-046: a write stages through a name of its own, so the loser of a race
    loses its document rather than mixing it into the winner's
-   ~~Per-host politeness for downloads.~~ Done (ADR-046) — as a count rather
    than a schedule: `downloads_per_host` bounds how many transfers one server
    faces at once, which is what let the worker count rise honestly. What a
    schedule would add on top is *spacing* — a wait between two requests to the
    same host, the way the crawler honours `Crawl-delay`. Nothing has asked for
    it yet: a download is one long request rather than a burst of short ones,
    which is the case a delay is for
-   Filtering and sorting the *crawl* list. The link and page tables inside one
    report have it since 0.14; the list of crawls itself is still everything in
    the order it was recorded. Still the point at which htmx would earn being
    vendored, and now with a measurement behind that: 0.15 paid the cheap half
    once, for the queue page, and it came to about forty lines and no
    dependency (ADR-038). The question is how many more places want their own
    forty lines
-   Authentication, before the interface is anything but loopback. Until then
    `serve` refuses a public address unless `--allow-remote` asks for it. More
    pressing since 0.16, because a button deletes files now: the same-origin
    check (ADR-043) stops a page somebody else wrote from pressing it through a
    browser, and stops nobody who can reach the port directly. It is also what
    the maintenance page is shaped around — it prints a command rather than
    running one, because there is nobody to ask who is pressing (ADR-045), and
    that trade would be worth revisiting once there is
-   Further providers: Pixeldrain, GoFile, MediaFire. Less urgent than they
    were: a share on one of those still needs its own provider to be *read*,
    but every ordinary file on the web is now fetched by `DirectProvider`
    (ADR-036), which is what most crawl results actually point at
-   ~~Parallel downloads~~ — done (ADR-046), and the "needs no other change"
    this entry promised was wrong: every download writes the library descriptor,
    and every write went through one temporary name, so the first thing two
    workers did was race for one file. Worth remembering the next time an entry
    here says a change is free
-   Resume — HTTP range requests plus a byte offset in the metadata record; the
    staging directory already keeps a partial file out of the library
-   Provider-side integrity verification beside the recorded SHA-256, starting
    with Mega's meta-MAC
-   Persisting inspections so dead links can be detected over time

## Long-term

-   Distributed crawling
-   Plugin marketplace
-   Multi-user support, which is the same subject as authentication
