# MaxiCrawler Vision

## Mission

MaxiCrawler is intended to become a professional, extensible platform
for discovering, organizing, analyzing and processing publicly
accessible or user-authorized links.

It is **not** just a downloader. Its primary purpose is to build a
high-quality link discovery and management platform with a clean,
modular architecture.

------------------------------------------------------------------------

## Long-Term Goals

-   Discover links from multiple sources.
-   Normalize and classify URLs.
-   Store metadata in a structured way.
-   Support plugins for different providers.
-   Provide powerful filtering and search.
-   Enable automated workflows.
-   Offer both CLI and GUI experiences.
-   Expose a stable API for integrations.

------------------------------------------------------------------------

## Core Principles

### Clean Architecture

Business logic remains independent from infrastructure.

### Plugin First

Provider-specific behavior belongs in plugins, not in the application
core.

### Testability

Every important component should be testable in isolation.

### Stability

The default branch should remain deployable and protected by automated
quality checks.

### Documentation

Architecture decisions are documented and kept up to date.

------------------------------------------------------------------------

## What MaxiCrawler is NOT

MaxiCrawler should not:

-   become tightly coupled to a single file host,
-   rely on undocumented provider-specific behavior,
-   bypass authentication, DRM, captchas or other protection mechanisms,
-   encourage downloading content without authorization,
-   sacrifice maintainability for short-term convenience.

The third of those has been tested against a real host rather than left as a
principle. MaxiCrawler carries a session a person exported from their own
browser, because that is the person's own authorization being used (ADR-047).
It does not obtain one, and when a bot check answered a page instead of the
page, the work stopped there and the feature changed shape (ADR-048). TLS
impersonation, a headless browser driven through an interstitial, and
anti-detection tooling are all technically available and all excluded by the
line above.

------------------------------------------------------------------------

## Success Criteria

A successful MaxiCrawler release is:

-   well documented,
-   well tested,
-   modular,
-   easy to extend,
-   pleasant to contribute to,
-   reliable for long-term maintenance.

------------------------------------------------------------------------

## Community Values

We welcome contributions that improve quality, maintainability and
usability.

Every pull request should leave the project better than it was before.
