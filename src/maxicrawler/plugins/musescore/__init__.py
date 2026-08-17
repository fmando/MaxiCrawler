"""The MuseScore plugin: score-address parsing and classification."""

from maxicrawler.plugins.musescore.parser import (
    MUSESCORE_DOMAIN,
    ScoreLink,
    parse_score_url,
)
from maxicrawler.plugins.musescore.plugin import (
    MUSESCORE_PLUGIN_NAME,
    MUSESCORE_PLUGIN_PRIORITY,
    MuseScorePlugin,
)

__all__ = [
    "MUSESCORE_DOMAIN",
    "MUSESCORE_PLUGIN_NAME",
    "MUSESCORE_PLUGIN_PRIORITY",
    "MuseScorePlugin",
    "ScoreLink",
    "parse_score_url",
]
