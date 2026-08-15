# Development guide

## Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

Four optional extras exist: `mega` (cryptography), `brotli`, `web` (the browser
interface), and `thumbnails` (Pillow). Each is part of the `dev` dependencies,
so the setup below installs all of them.

The tests that need Pillow skip themselves without it rather than failing, since
the feature it serves is optional in the same way — but a run that skips them
has not tested the thumbnails.

## Setup

```bash
uv sync --all-extras
uv run pre-commit install
```

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src scripts
uv run pytest
```

All four commands must pass before a pull request is opened. The maintenance
scripts in `scripts/` are checked alongside the package: they are not part of
what is shipped, but they are read and edited like anything else here.

## Tests and the network

The suite makes no outbound connections. Provider tests drive a recording
transport double, and the HTTP transport itself is exercised against a
throwaway server on `127.0.0.1`, so the gates pass on an isolated machine.

Mega fixtures are generated rather than recorded: `tests/mega_fixtures.py`
builds and encrypts the payloads, so the repository carries no third-party
content and every ciphertext round-trips against the functions under test.

The `mega` extra (`cryptography`) is part of the `dev` dependencies, so
`uv sync --all-extras` is enough to run everything.

Download tests write only into pytest's `tmp_path`, and the download manager is
driven against a stub provider, so a full run touches neither the network nor
anything outside the temporary directory.

Since the crawler became recursive, "no outbound connections" is no longer
free: links are followed off-host by default, so a fixture holding a real URL
turns the suite into a client of somebody else's server. It happened once while
Sprint 9 was being written.

Two things prevent a repeat. A fixture reaches "elsewhere" through the *same*
local server under its other hostname — `127.0.0.1` and `localhost` are one
machine but two hosts, which exercises the scope rule without leaving it. And
`tests/test_no_outbound_connections.py` guards `socket.create_connection`, so
the mistake fails loudly rather than silently.

The web crawler is tested against a throwaway server on `127.0.0.1`
(`tests/web_server.py`) rather than a mocked `urllib`. Redirects, compressed
bodies, and content-type refusals are exactly where the bugs live, and none of
them survives being stubbed out. The `brotli` extra is part of the `dev`
dependencies; without it the handful of Brotli tests skip and the rest still
pass, which is the same situation a user without the extra is in.

The web interface is driven through Starlette's `TestClient`, which speaks to
the application object rather than to a port, so no test binds a socket. The one
place that would is `uvicorn.run`, and `tests/test_cli_serve.py` replaces it and
inspects what it was asked to do — which is also the only thing worth asserting
about it.

## Asserting on command-line output

Typer renders help and usage errors through Rich, and Rich's highlighter styles
an option name in pieces. Colour is off on a developer's machine and forced on
under GitHub Actions, where `--allow-remote` then leaves as `-`, `-allow` and
`-remote` with escape sequences between them — so a substring check against the
raw output passes locally and fails only in CI.

Strip the styling before asserting, as `plain()` in `tests/test_cli_serve.py`
does. What a person reads is the same either way, and that is what such a test
is about.

Running the suite with `GITHUB_ACTIONS=true` reproduces it locally.
