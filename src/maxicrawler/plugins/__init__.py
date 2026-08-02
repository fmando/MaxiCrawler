"""Plugin extension points."""

from maxicrawler.plugins.base import Plugin
from maxicrawler.plugins.defaults import create_default_registry
from maxicrawler.plugins.generic import (
    GENERIC_PLUGIN_NAME,
    GENERIC_PLUGIN_PRIORITY,
    GenericPlugin,
)
from maxicrawler.plugins.loader import PluginLoader
from maxicrawler.plugins.mega import (
    MEGA_PLUGIN_NAME,
    MEGA_PLUGIN_PRIORITY,
    MegaLink,
    MegaLinkFormat,
    MegaLinkKind,
    MegaPlugin,
    parse_mega_url,
)
from maxicrawler.plugins.protocol import CrawlerPlugin
from maxicrawler.plugins.registry import (
    DuplicatePluginError,
    InvalidPluginError,
    PluginRegistry,
    PluginRegistryError,
    UnknownPluginError,
)
from maxicrawler.plugins.resolver import PluginResolver

__all__ = [
    "GENERIC_PLUGIN_NAME",
    "GENERIC_PLUGIN_PRIORITY",
    "MEGA_PLUGIN_NAME",
    "MEGA_PLUGIN_PRIORITY",
    "CrawlerPlugin",
    "DuplicatePluginError",
    "GenericPlugin",
    "InvalidPluginError",
    "MegaLink",
    "MegaLinkFormat",
    "MegaLinkKind",
    "MegaPlugin",
    "Plugin",
    "PluginLoader",
    "PluginRegistry",
    "PluginRegistryError",
    "PluginResolver",
    "UnknownPluginError",
    "create_default_registry",
    "parse_mega_url",
]
