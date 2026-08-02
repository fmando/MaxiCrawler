"""Domain event value objects."""

from dataclasses import dataclass

from maxicrawler.domain import DownloadTask, PluginInfo, ScanSession, Statistics, UrlRecord


@dataclass(frozen=True, slots=True)
class UrlDiscovered:
    record: UrlRecord


@dataclass(frozen=True, slots=True)
class ScanStarted:
    session: ScanSession


@dataclass(frozen=True, slots=True)
class ScanFinished:
    session: ScanSession
    statistics: Statistics


@dataclass(frozen=True, slots=True)
class PluginLoaded:
    plugin: PluginInfo


@dataclass(frozen=True, slots=True)
class DownloadQueued:
    task: DownloadTask


@dataclass(frozen=True, slots=True)
class DownloadFinished:
    task: DownloadTask


@dataclass(frozen=True, slots=True)
class DownloadFailed:
    task: DownloadTask
    reason: str


Event = (
    UrlDiscovered
    | ScanStarted
    | ScanFinished
    | PluginLoaded
    | DownloadQueued
    | DownloadFinished
    | DownloadFailed
)
