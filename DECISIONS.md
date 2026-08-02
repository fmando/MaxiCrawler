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
