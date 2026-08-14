"""TOML-backed configuration models."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("maxicrawler.toml")


@dataclass(frozen=True, slots=True)
class Settings:
    """Application settings loaded from a TOML document.

    The network fields are used only by the commands that talk to a provider.
    Discovery never reads them, because discovery never leaves the machine.
    """

    user_agent: str = "MaxiCrawler/0.1.0"
    database_path: Path = Path("maxicrawler.db")
    library_path: Path = Path("library")
    """Where downloads are stored; ``--output`` overrides it for one run."""

    log_level: str = "INFO"
    network_timeout: float = 30.0
    network_retries: int = 3
    max_entries: int = 1000

    max_page_bytes: int = 8 * 1024 * 1024
    """Upper bound on a crawled page, before and after decompression."""

    max_redirects: int = 5
    """How many hops one fetch may follow before the chain is called a loop."""

    max_links: int = 10_000
    """How many links one page may contribute before the rest are dropped."""

    max_view_bytes: int = 32 * 1024 * 1024
    """Largest stored file the web interface will show inline.

    A browser handed a 400 MB text file stops answering, so above this a page
    offers the download instead of the file. It bounds what is *displayed*;
    nothing about what may be stored.
    """

    max_stream_bytes: int = 0
    """Largest audio or video file the interface will play, or 0 for no limit.

    Its own bound rather than :attr:`max_view_bytes`, because the two limits
    exist for opposite reasons. That one is about a browser being handed a whole
    file at once: a 400 MB text document has to arrive and be laid out before
    anything appears. An ``<audio>`` or ``<video>`` element does not work that
    way — it asks for the ranges it needs, plays from the first of them, and
    never holds the file. A two-gigabyte recording therefore starts as quickly as
    a two-megabyte one, and refusing it would be applying a rule whose reason
    does not reach it.

    Unbounded by default for the same reason, and configurable anyway: what a
    person is really bounding here is a network they share, not a browser they
    might hang.
    """

    preview_inline_bytes: int = 1_000_000
    """Largest image a tile shows as itself, in bytes.

    A grid of sixty tiles that each loaded the original is a page that transfers
    what sixty originals weigh — and this server does not usually run on the
    machine somebody is looking at it from, so those bytes cross a network. The
    second cost is worse and is not a file size at all: a browser holds a decoded
    image as a bitmap of four bytes per pixel, so a 300 kB photograph at
    6000×4000 occupies roughly 96 MB while it is on screen.

    Above the limit a tile shows a symbol and the size, never a scaled-down
    original — scaling in the browser means the original was transferred and
    decoded first, which is precisely the cost being avoided.

    One megabyte is a starting value covering the web-sized pictures a crawl
    mostly returns, and is meant to be measured against a real library rather
    than trusted. Zero shows no image in any tile.
    """

    min_download_size: int = 100_000
    """Smallest payload worth putting in the library, in bytes.

    100 kB, decimal, the same unit sizes are shown in. A crawl of an image
    directory returns a thumbnail, a sprite and an icon for every picture worth
    having, and clearing those out by hand does not work: the next bulk queue
    fetches them again, because "the library holds it" is answered by a record
    and a file, and both are gone.

    It is applied in the download sink, so it applies to every transfer — one
    clicked by hand as much as one out of a batch. Two rules would be two
    explanations, and the sink does not know who asked.

    Zero switches it off. Nothing is ever silently dropped either way: a refused
    payload is recorded with both sizes and appears in the library under its own
    state, which is the whole difference between a limit and a disappearance.
    """

    crawl_depth: int = 0
    """Default link distance a crawl follows; zero fetches the seed alone."""

    crawl_max_pages: int = 50
    """Default ceiling on how many pages one crawl fetches."""

    crawl_same_domain: bool = False
    """Whether a crawl stays on the seed's host unless told otherwise.

    Off, so that following a share link to Mega or Pixeldrain keeps working.
    Configurable here rather than hard-coded, because which of the two
    workflows an installation mostly serves is an installation's business.
    """

    crawl_below_seed: bool = False
    """Whether a crawl stays at or below the place the start URL names.

    Off, for the same reason ``crawl_same_domain`` is: the narrower rule breaks
    the share-link workflow outright. On, it supersedes ``crawl_same_domain``
    — an installation whose work is walking one section of one site sets this
    and stops ticking a box on every crawl.
    """

    direct_downloads: bool = True
    """Whether files at ordinary URLs may be downloaded at all.

    On, because it is the difference between a report full of links and a
    library full of files: without it only a host with a provider of its own —
    today, Mega — can be fetched, and an image is something to look at rather
    than something to keep.

    Off leaves every other provider working and turns
    :class:`~maxicrawler.providers.direct.DirectProvider` into one that
    advertises nothing, so a report simply offers no download beside an
    ordinary link. That is a real configuration rather than a courtesy: this is
    the one provider that can be pointed at any host a crawl named, and an
    installation is entitled to say no to that in one place.

    It is not a safety setting. What keeps a download off this machine and this
    network is the private-network rule, which applies either way.
    """

    respect_robots: bool = True
    """Whether a crawl obeys the ``/robots.txt`` of the hosts it visits.

    On. A crawl follows links now, and something that fetches many pages
    unattended is a bot however it was started; robots.txt is the convention
    bots are held to. It is also never silent — a refused URL is counted under
    its own reason in the report — and it is one flag to turn off for the run
    where somebody has decided otherwise.

    This governs *crawling* only. A download is an explicit act on a resource a
    person named, and no provider consults robots.txt.
    """

    robots_user_agent: str = ""
    """Which product token ``robots.txt`` groups are matched against.

    Empty means "derive it from :attr:`user_agent`", which is right unless the
    header has been customized into something whose first word is not this
    crawler's name.
    """

    robots_timeout: float = 10.0
    """Seconds to wait for a ``robots.txt``.

    Shorter than :attr:`network_timeout`, because this request is overhead
    rather than the work, and a host that is slow to serve one file should cost
    a crawl seconds rather than half a minute per host.
    """

    robots_deny_on_error: bool = True
    """Whether a ``robots.txt`` we could not reach means "do not crawl".

    RFC 9309 says to assume complete disallow when a host answers 5xx or does
    not answer at all: not knowing what a site permits is not permission. Off
    turns that into "carry on", which is a decision an operator may make and
    this program may not make for them.
    """

    crawl_delay: float = 0.0
    """Seconds to leave between two requests to the same host.

    Zero: no artificial delay nobody asked for. A host that wants to be crawled
    slowly says so in its ``robots.txt``, and that *is* honoured — see
    :attr:`respect_crawl_delay`. Raise this to be slower than any site asked.
    """

    respect_crawl_delay: bool = True
    """Whether a host's own ``Crawl-delay`` is honoured when it states one."""

    max_crawl_delay: float = 30.0
    """The longest ``Crawl-delay`` that will be obeyed before it is clamped.

    A stranger's file must not be able to freeze a crawl. Above this, the
    request is spaced by this instead — which is still polite and still ends.
    """

    allow_private_networks: bool = False
    """Whether a crawl may reach loopback, this network, or link-local space.

    Off. The web interface accepts a URL from whoever is looking at it, and a
    browser can be pointed at a form by any page it visits, so *"fetch
    http://localhost:9200/"* is a request that arrives on its own. Turning this
    on stays possible for an operator crawling their own network — and never
    opens a cloud metadata service, which is a different decision.
    """

    private_network_allowlist: tuple[str, ...] = ()
    """Hosts, addresses, or CIDR blocks exempt from the private-network rule.

    The fine-grained escape, so that crawling one machine on a home network
    does not mean opening the whole of it. An entry may be a host name
    (``wiki.local``), an address (``192.168.1.20``), or a block
    (``10.0.0.0/8``).
    """

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            msg = "user_agent must not be empty"
            raise ValueError(msg)
        if not str(self.library_path).strip():
            msg = "library_path must not be empty"
            raise ValueError(msg)
        if not self.log_level.strip():
            msg = "log_level must not be empty"
            raise ValueError(msg)
        if self.network_timeout <= 0:
            msg = "network_timeout must be positive"
            raise ValueError(msg)
        if self.network_retries < 1:
            msg = "network_retries must be at least 1"
            raise ValueError(msg)
        if self.max_entries < 1:
            msg = "max_entries must be at least 1"
            raise ValueError(msg)
        if self.max_page_bytes < 1:
            msg = "max_page_bytes must be at least 1"
            raise ValueError(msg)
        if self.max_redirects < 0:
            msg = "max_redirects must not be negative"
            raise ValueError(msg)
        if self.max_links < 1:
            msg = "max_links must be at least 1"
            raise ValueError(msg)
        if self.max_view_bytes < 1:
            msg = "max_view_bytes must be at least 1"
            raise ValueError(msg)
        if self.max_stream_bytes < 0:
            msg = "max_stream_bytes must not be negative"
            raise ValueError(msg)
        if self.preview_inline_bytes < 0:
            msg = "preview_inline_bytes must not be negative"
            raise ValueError(msg)
        if self.min_download_size < 0:
            msg = "min_download_size must not be negative"
            raise ValueError(msg)
        if self.crawl_depth < 0:
            msg = "crawl_depth must not be negative"
            raise ValueError(msg)
        if self.crawl_max_pages < 1:
            msg = "crawl_max_pages must be at least 1"
            raise ValueError(msg)
        if self.robots_timeout <= 0:
            msg = "robots_timeout must be positive"
            raise ValueError(msg)
        if self.crawl_delay < 0:
            msg = "crawl_delay must not be negative"
            raise ValueError(msg)
        if self.max_crawl_delay < 0:
            msg = "max_crawl_delay must not be negative"
            raise ValueError(msg)

    @classmethod
    def from_toml(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Settings":
        """Load settings from *path*, returning defaults when it does not exist."""
        if not path.exists():
            return cls()
        with path.open("rb") as config_file:
            raw_config: dict[str, Any] = tomllib.load(config_file)
        app_config = raw_config.get("maxicrawler", raw_config)
        if not isinstance(app_config, dict):
            msg = "[maxicrawler] must be a TOML table"
            raise ValueError(msg)
        defaults = cls()
        return cls(
            user_agent=_string_value(app_config, "user_agent", defaults.user_agent),
            database_path=Path(
                _string_value(app_config, "database_path", str(defaults.database_path))
            ),
            library_path=Path(
                _string_value(app_config, "library_path", str(defaults.library_path))
            ),
            log_level=_string_value(app_config, "log_level", defaults.log_level).upper(),
            network_timeout=_float_value(app_config, "network_timeout", defaults.network_timeout),
            network_retries=_int_value(app_config, "network_retries", defaults.network_retries),
            max_entries=_int_value(app_config, "max_entries", defaults.max_entries),
            max_page_bytes=_int_value(app_config, "max_page_bytes", defaults.max_page_bytes),
            max_redirects=_int_value(app_config, "max_redirects", defaults.max_redirects),
            max_links=_int_value(app_config, "max_links", defaults.max_links),
            max_view_bytes=_int_value(app_config, "max_view_bytes", defaults.max_view_bytes),
            max_stream_bytes=_int_value(app_config, "max_stream_bytes", defaults.max_stream_bytes),
            preview_inline_bytes=_int_value(
                app_config, "preview_inline_bytes", defaults.preview_inline_bytes
            ),
            min_download_size=_int_value(
                app_config, "min_download_size", defaults.min_download_size
            ),
            crawl_depth=_int_value(app_config, "crawl_depth", defaults.crawl_depth),
            crawl_max_pages=_int_value(app_config, "crawl_max_pages", defaults.crawl_max_pages),
            crawl_same_domain=_bool_value(
                app_config, "crawl_same_domain", defaults.crawl_same_domain
            ),
            crawl_below_seed=_bool_value(app_config, "crawl_below_seed", defaults.crawl_below_seed),
            direct_downloads=_bool_value(app_config, "direct_downloads", defaults.direct_downloads),
            respect_robots=_bool_value(app_config, "respect_robots", defaults.respect_robots),
            robots_user_agent=_string_value(
                app_config, "robots_user_agent", defaults.robots_user_agent
            ),
            robots_timeout=_float_value(app_config, "robots_timeout", defaults.robots_timeout),
            robots_deny_on_error=_bool_value(
                app_config, "robots_deny_on_error", defaults.robots_deny_on_error
            ),
            crawl_delay=_float_value(app_config, "crawl_delay", defaults.crawl_delay),
            respect_crawl_delay=_bool_value(
                app_config, "respect_crawl_delay", defaults.respect_crawl_delay
            ),
            max_crawl_delay=_float_value(app_config, "max_crawl_delay", defaults.max_crawl_delay),
            allow_private_networks=_bool_value(
                app_config, "allow_private_networks", defaults.allow_private_networks
            ),
            private_network_allowlist=_string_list_value(
                app_config, "private_network_allowlist", defaults.private_network_allowlist
            ),
        )

    def to_toml(self) -> str:
        """Serialize the settings as a human-editable TOML document."""
        return (
            "[maxicrawler]\n"
            f'user_agent = "{self.user_agent}"\n'
            f'database_path = "{self.database_path.as_posix()}"\n'
            f'library_path = "{self.library_path.as_posix()}"\n'
            f'log_level = "{self.log_level}"\n'
            f"network_timeout = {self.network_timeout}\n"
            f"network_retries = {self.network_retries}\n"
            f"max_entries = {self.max_entries}\n"
            f"max_page_bytes = {self.max_page_bytes}\n"
            f"max_redirects = {self.max_redirects}\n"
            f"max_links = {self.max_links}\n"
            f"max_view_bytes = {self.max_view_bytes}\n"
            f"preview_inline_bytes = {self.preview_inline_bytes}\n"
            f"min_download_size = {self.min_download_size}\n"
            f"crawl_depth = {self.crawl_depth}\n"
            f"crawl_max_pages = {self.crawl_max_pages}\n"
            f"crawl_same_domain = {str(self.crawl_same_domain).lower()}\n"
            f"crawl_below_seed = {str(self.crawl_below_seed).lower()}\n"
            f"direct_downloads = {str(self.direct_downloads).lower()}\n"
            f"respect_robots = {str(self.respect_robots).lower()}\n"
            f'robots_user_agent = "{self.robots_user_agent}"\n'
            f"robots_timeout = {self.robots_timeout}\n"
            f"robots_deny_on_error = {str(self.robots_deny_on_error).lower()}\n"
            f"crawl_delay = {self.crawl_delay}\n"
            f"respect_crawl_delay = {str(self.respect_crawl_delay).lower()}\n"
            f"max_crawl_delay = {self.max_crawl_delay}\n"
            f"allow_private_networks = {str(self.allow_private_networks).lower()}\n"
            f"private_network_allowlist = {_toml_array(self.private_network_allowlist)}\n"
        )


def _string_value(values: dict[str, Any], key: str, default: str) -> str:
    value = values.get(key, default)
    if not isinstance(value, str):
        msg = f"{key} must be a string"
        raise ValueError(msg)
    return value


def _int_value(values: dict[str, Any], key: str, default: int) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key} must be an integer"
        raise ValueError(msg)
    return value


def _bool_value(values: dict[str, Any], key: str, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        msg = f"{key} must be true or false"
        raise ValueError(msg)
    return value


def _string_list_value(
    values: dict[str, Any], key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    value = values.get(key, default)
    if isinstance(value, str) or not isinstance(value, list | tuple):
        msg = f"{key} must be a list of strings"
        raise ValueError(msg)
    if any(not isinstance(entry, str) for entry in value):
        msg = f"{key} must be a list of strings"
        raise ValueError(msg)
    return tuple(value)


def _toml_array(values: tuple[str, ...]) -> str:
    """Return *values* as a TOML array of strings."""
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


def _float_value(values: dict[str, Any], key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{key} must be a number"
        raise ValueError(msg)
    return float(value)
