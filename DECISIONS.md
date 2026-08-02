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
