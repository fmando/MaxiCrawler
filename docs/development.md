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
