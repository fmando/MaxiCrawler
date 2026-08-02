"""The Mega provider plugin: models, URL parsing, and classification."""

from maxicrawler.plugins.mega.models import MegaLink, MegaLinkFormat, MegaLinkKind
from maxicrawler.plugins.mega.parser import HANDLE, KEY, MEGA_HOSTS, parse_mega_url
from maxicrawler.plugins.mega.plugin import (
    MEGA_PLUGIN_NAME,
    MEGA_PLUGIN_PRIORITY,
    MegaPlugin,
)

__all__ = [
    "HANDLE",
    "KEY",
    "MEGA_HOSTS",
    "MEGA_PLUGIN_NAME",
    "MEGA_PLUGIN_PRIORITY",
    "MegaLink",
    "MegaLinkFormat",
    "MegaLinkKind",
    "MegaPlugin",
    "parse_mega_url",
]
