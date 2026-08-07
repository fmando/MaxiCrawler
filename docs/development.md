# Development guide

## Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync --all-extras
uv run pre-commit install
```

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

All four commands must pass before a pull request is opened.

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

The web crawler is tested against a throwaway server on `127.0.0.1`
(`tests/web_server.py`) rather than a mocked `urllib`. Redirects, compressed
bodies, and content-type refusals are exactly where the bugs live, and none of
them survives being stubbed out. The `brotli` extra is part of the `dev`
dependencies; without it the handful of Brotli tests skip and the rest still
pass, which is the same situation a user without the extra is in.
