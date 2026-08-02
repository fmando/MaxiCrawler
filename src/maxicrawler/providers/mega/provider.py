"""The Mega resource provider.

The plugin in :mod:`maxicrawler.plugins.mega` decides whether a URL is a Mega
share; this provider decides what can be done with the share behind it. It
reuses the plugin's parser so the URL grammar has a single home.

Inspection and transfer are kept strictly apart, because they cost different
things. An inspection asks Mega to describe a resource and deliberately leaves
the download flag unset, so no transfer URL is allocated and no quota is
consumed. Only :meth:`MegaProvider.download` sets it, and only when content is
actually about to be fetched.
"""

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from contextlib import closing
from typing import Any
from urllib.parse import urlsplit

from maxicrawler import __version__
from maxicrawler.domain import (
    Availability,
    ContentDescriptor,
    LinkAttribute,
    ProviderCapability,
    ProviderInfo,
    ResourceEntry,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
    ResourceSecret,
    UrlClassification,
)
from maxicrawler.plugins.mega import MegaLink, MegaLinkKind, parse_mega_url
from maxicrawler.providers.crypto import INSTALL_HINT, CipherBackend
from maxicrawler.providers.errors import (
    ProviderCryptoError,
    ProviderDependencyError,
    ProviderRateLimitError,
    ProviderTransportError,
    UnsupportedResourceError,
)
from maxicrawler.providers.mega.api import MegaApiClient, MegaApiError, transfer_url
from maxicrawler.providers.mega.crypto import (
    MegaFileKey,
    attribute_name,
    decode_base64,
    decrypt_attributes,
    decrypt_node_key,
    node_aes_key,
    select_key_ciphertext,
    unpack_file_key,
    unpack_folder_key,
)
from maxicrawler.providers.mega.download import decrypt_content
from maxicrawler.providers.mega.mapping import (
    NODE_ROOT,
    availability_for,
    node_kind,
    node_size,
    node_timestamp,
)
from maxicrawler.providers.protocol import DownloadSink
from maxicrawler.providers.transport import DEFAULT_CHUNK_SIZE, StreamTransport
from maxicrawler.utils.urls import strip_fragment

MEGA_PROVIDER_NAME = "mega"
"""Registry name of the Mega provider."""

MEGA_PROVIDER_PRIORITY = 100
"""Default priority, matching the Mega plugin."""

DEFAULT_MAX_ENTRIES = 1000
"""How many entries of a shared folder an inspection reports by default."""

_KINDS = {MegaLinkKind.FILE: ResourceKind.FILE, MegaLinkKind.FOLDER: ResourceKind.FOLDER}


class MegaProvider:
    """Reads and transfers Mega file and folder shares.

    A file share is described by one ``g`` request that omits the download
    flag, so Mega reports size and encrypted attributes without allocating a
    transfer. A folder share is described by one ``f`` request that returns the
    whole node tree; sizes, timestamps, and structure arrive in plaintext, and
    only names need the key from the link.

    A transfer is the same ``g`` request with the download flag set, followed
    by a stream of AES-128-CTR ciphertext that is decrypted chunk by chunk on
    the way to the sink. Nothing is buffered: a fifty-gigabyte share costs the
    same memory as a fifty-kilobyte one.

    Transferring is optional. Without a stream transport, or without the AES
    backend, the provider simply does not advertise
    :attr:`~maxicrawler.domain.providers.ProviderCapability.DOWNLOAD` —
    inspection keeps working unchanged.

    The key never leaves this process. It is used by
    :mod:`maxicrawler.providers.mega.crypto` and
    :mod:`maxicrawler.providers.mega.download`, and is never part of a request,
    which is exactly the property a Mega share link is built on.
    """

    def __init__(
        self,
        api: MegaApiClient,
        *,
        cipher: CipherBackend | None = None,
        stream: StreamTransport | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        priority: int = MEGA_PROVIDER_PRIORITY,
    ) -> None:
        if max_entries < 1:
            msg = "max_entries must be at least 1"
            raise ValueError(msg)
        if chunk_size < 1:
            msg = "chunk_size must be at least 1"
            raise ValueError(msg)
        self._api = api
        self._cipher = cipher
        self._stream = stream
        self._max_entries = max_entries
        self._chunk_size = chunk_size
        capabilities = {ProviderCapability.INSPECT, ProviderCapability.LIST}
        if stream is not None and cipher is not None:
            capabilities.add(ProviderCapability.DOWNLOAD)
        self._metadata = ProviderInfo(
            name=MEGA_PROVIDER_NAME,
            version=__version__,
            module=__name__,
            description="Reads and transfers Mega file and folder shares.",
            display_name="Mega",
            priority=priority,
            capabilities=frozenset(capabilities),
        )

    @property
    def metadata(self) -> ProviderInfo:
        """Return the immutable descriptor for this provider."""
        return self._metadata

    def supports(self, classification: UrlClassification) -> bool:
        """Return whether *classification* points at a Mega share link.

        The original URL is parsed rather than the plugin's verdict, so the
        provider stays usable with any classifier that recognises the link.
        """
        return parse_mega_url(classification.record.raw_url) is not None

    def reference(self, classification: UrlClassification) -> ResourceRef:
        """Return the Mega resource *classification* addresses, without any I/O."""
        raw_url = classification.record.raw_url
        link = parse_mega_url(raw_url)
        if link is None:
            msg = f"not a Mega share link: {strip_fragment(raw_url)}"
            raise UnsupportedResourceError(msg)
        secret = None if link.key is None else ResourceSecret(link.key)
        url = share_url(raw_url, link)
        if link.node_handle is not None:
            return ResourceRef(
                provider=MEGA_PROVIDER_NAME,
                resource_id=link.node_handle,
                kind=_KINDS.get(link.node_kind, ResourceKind.UNKNOWN)
                if link.node_kind is not None
                else ResourceKind.UNKNOWN,
                url=url,
                secret=secret,
                parent_id=link.handle,
            )
        return ResourceRef(
            provider=MEGA_PROVIDER_NAME,
            resource_id=link.handle,
            kind=_KINDS[link.kind],
            url=url,
            secret=secret,
        )

    def inspect(self, ref: ResourceRef) -> ResourceInspection:
        """Return what Mega discloses about *ref* without transferring content."""
        if ref.provider != MEGA_PROVIDER_NAME:
            msg = f"reference belongs to another provider: {ref.provider}"
            raise UnsupportedResourceError(msg)
        try:
            return self._inspect(ref)
        except MegaApiError as error:
            return ResourceInspection(ref=ref, availability=availability_for(error.code))
        except ProviderRateLimitError:
            return ResourceInspection(ref=ref, availability=Availability.RATE_LIMITED)

    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor:
        """Stream the content of *ref* into *sink*, decrypting it on the way.

        A Mega share is end-to-end encrypted, so a link published without its
        key describes a resource that can be listed but not read. That is
        reported as an unsupported reference rather than attempted, because no
        amount of transferring would produce usable bytes.
        """
        if ref.provider != MEGA_PROVIDER_NAME:
            msg = f"reference belongs to another provider: {ref.provider}"
            raise UnsupportedResourceError(msg)
        if ref.kind is ResourceKind.FOLDER:
            msg = f"a Mega folder is not a transfer; download its entries: {ref.url}"
            raise UnsupportedResourceError(msg)
        secret = ref.secret
        if secret is None:
            msg = f"the link carries no decryption key, so its content stays sealed: {ref.url}"
            raise UnsupportedResourceError(msg)
        stream = self._require_stream()
        cipher = self._require_cipher()
        key = self._content_key(ref, secret, cipher)
        answer = self._transfer_answer(ref)
        descriptor = ContentDescriptor(
            name=self._decrypt_name(answer.get("at"), key.aes_key, cipher),
            size=node_size(answer.get("s")),
        )
        sink.begin(descriptor)
        with closing(stream.stream(transfer_url(answer), chunk_size=self._chunk_size)) as chunks:
            for block in decrypt_content(cipher, key, chunks):
                sink.write(block)
        return descriptor

    def _transfer_answer(self, ref: ResourceRef) -> Mapping[str, Any]:
        """Ask Mega to allocate a transfer for *ref*.

        This is the request that costs quota, so it is made last: a reference
        whose key cannot be resolved never reaches it.
        """
        if ref.parent_id is None:
            return self._api.file_transfer(ref.resource_id)
        return self._api.node_transfer(ref.resource_id, folder=ref.parent_id)

    def _content_key(
        self, ref: ResourceRef, secret: ResourceSecret, cipher: CipherBackend
    ) -> MegaFileKey:
        """Return the key that decrypts the content of *ref*.

        For a file link the credential *is* the file key. For an entry inside a
        shared folder it is the share key, and the per-node key it opens is
        published only in the folder listing — which is why a contained
        download costs one request more than a plain one.
        """
        if ref.parent_id is None:
            return unpack_file_key(decode_base64(secret.reveal()))
        share_key = unpack_folder_key(decode_base64(secret.reveal()))
        node = _find_node(self._api.folder_nodes(ref.parent_id), ref.resource_id)
        if node is None:
            msg = f"the shared folder no longer lists this entry: {ref.resource_id}"
            raise UnsupportedResourceError(msg)
        encoded = node.get("k")
        ciphertext = (
            select_key_ciphertext(encoded, ref.parent_id) if isinstance(encoded, str) else None
        )
        if ciphertext is None:
            msg = "the shared folder publishes no usable key for this entry"
            raise ProviderCryptoError(msg)
        return unpack_file_key(decrypt_node_key(cipher, share_key, ciphertext))

    def _inspect(self, ref: ResourceRef) -> ResourceInspection:
        """Route *ref* to the request that can describe it."""
        if ref.parent_id is not None:
            return self._inspect_share(ref, share=ref.parent_id, target=ref.resource_id)
        if ref.kind is ResourceKind.FOLDER:
            return self._inspect_share(ref, share=ref.resource_id, target=ref.resource_id)
        return self._inspect_file(ref)

    def _inspect_file(self, ref: ResourceRef) -> ResourceInspection:
        """Describe a public file share."""
        answer = self._api.file_metadata(ref.resource_id)
        name, readable = self._file_name(answer.get("at"), ref.secret)
        metadata = ResourceMetadata(
            kind=ResourceKind.FILE,
            name=name,
            size=node_size(answer.get("s")),
            attributes=(LinkAttribute("handle", ref.resource_id),),
        )
        return ResourceInspection(
            ref=ref,
            availability=Availability.AVAILABLE,
            metadata=metadata,
            names_available=readable,
        )

    def _inspect_share(self, ref: ResourceRef, *, share: str, target: str) -> ResourceInspection:
        """Describe *target* inside the shared folder *share*.

        One listing answers both cases: a folder link describes its own root,
        and a link that selects one entry describes that entry. The listing is
        needed either way, because a contained node's key is published only
        there.
        """
        nodes = self._api.folder_nodes(share)
        node = _find_node(nodes, target) or (_find_root(nodes) if target == share else None)
        if node is None:
            return ResourceInspection(ref=ref, availability=Availability.NOT_FOUND)
        reader = self._name_reader(ref.secret, share)
        metadata = self._node_metadata(node, reader)
        entries: tuple[ResourceEntry, ...] = ()
        truncated = False
        if metadata.kind is ResourceKind.FOLDER:
            children = _descendants(nodes, _handle(node))
            entries, truncated = self._entries(ref, children, reader, share)
        return ResourceInspection(
            ref=ref,
            availability=Availability.AVAILABLE,
            metadata=metadata,
            entries=entries,
            names_available=reader.readable,
            truncated=truncated,
        )

    def _entries(
        self,
        ref: ResourceRef,
        children: Sequence[Mapping[str, Any]],
        reader: "_NameReader",
        share: str,
    ) -> tuple[tuple[ResourceEntry, ...], bool]:
        """Return the ordered entries of a container and whether they were cut."""
        entries = [
            ResourceEntry(
                ref=ResourceRef(
                    provider=MEGA_PROVIDER_NAME,
                    resource_id=_handle(child),
                    kind=node_kind(child.get("t")),
                    url=ref.url,
                    secret=ref.secret,
                    parent_id=share,
                ),
                metadata=self._node_metadata(child, reader),
            )
            for child in children
        ]
        entries.sort(key=_entry_order)
        return tuple(entries[: self._max_entries]), len(entries) > self._max_entries

    def _node_metadata(self, node: Mapping[str, Any], reader: "_NameReader") -> ResourceMetadata:
        """Return the metadata of one listed node."""
        return ResourceMetadata(
            kind=node_kind(node.get("t")),
            name=reader.name(node),
            size=node_size(node.get("s")),
            modified_at=node_timestamp(node.get("ts")),
            attributes=(LinkAttribute("handle", _handle(node)),),
        )

    def _file_name(self, payload: object, secret: ResourceSecret | None) -> tuple[str | None, bool]:
        """Return the name of a file share and whether names were readable."""
        if secret is None or not isinstance(payload, str):
            return None, False
        cipher = self._require_cipher()
        try:
            key = unpack_file_key(decode_base64(secret.reveal())).aes_key
        except ProviderCryptoError:
            return None, False
        name = self._decrypt_name(payload, key, cipher)
        return name, name is not None

    @staticmethod
    def _decrypt_name(payload: object, key: bytes, cipher: CipherBackend) -> str | None:
        """Return the name in an attribute block, or ``None`` if it stays hidden.

        A resource whose name cannot be read is still perfectly downloadable —
        the library falls back to a generic file name — so an undecryptable
        attribute block is an absent name rather than a failure.
        """
        if not isinstance(payload, str):
            return None
        try:
            return attribute_name(decrypt_attributes(cipher, key, payload))
        except ProviderCryptoError:
            return None

    def _name_reader(self, secret: ResourceSecret | None, share: str) -> "_NameReader":
        """Return the reader that decrypts names inside the share *share*."""
        if secret is None:
            return _NameReader(None, None, share)
        cipher = self._require_cipher()
        try:
            share_key = unpack_folder_key(decode_base64(secret.reveal()))
        except ProviderCryptoError:
            return _NameReader(None, None, share)
        return _NameReader(cipher, share_key, share)

    def _require_cipher(self) -> CipherBackend:
        """Return the cipher backend, or explain how to obtain it.

        A link that carries a key is a request to read names, so a missing
        optional dependency is reported rather than silently degraded.
        """
        if self._cipher is None:
            msg = f"reading Mega names requires the cryptography package; {INSTALL_HINT}"
            raise ProviderDependencyError(msg)
        return self._cipher

    def _require_stream(self) -> StreamTransport:
        """Return the transfer transport, or explain that there is none.

        Reaching this without one means the provider was composed for
        inspection only, which its metadata already said by omitting
        :attr:`~maxicrawler.domain.providers.ProviderCapability.DOWNLOAD`.
        """
        if self._stream is None:
            msg = "this Mega provider was built without a transfer transport"
            raise ProviderTransportError(msg)
        return self._stream


class _NameReader:
    """Decrypts the names of one shared folder, or reports that it cannot.

    A node whose key or attributes fail to decrypt yields no name instead of
    aborting the whole listing, so one damaged entry cannot hide a folder. If
    nothing at all decrypts, :attr:`readable` turns false and the inspection
    says so rather than presenting a nameless folder as fully read.
    """

    def __init__(
        self, cipher: CipherBackend | None, share_key: bytes | None, share_handle: str
    ) -> None:
        self._cipher = cipher
        self._share_key = share_key
        self._share_handle = share_handle
        self._attempts = 0
        self._successes = 0

    @property
    def readable(self) -> bool:
        """Return whether names could be decrypted."""
        if self._cipher is None or self._share_key is None:
            return False
        return self._attempts == 0 or self._successes > 0

    def name(self, node: Mapping[str, Any]) -> str | None:
        """Return the decrypted name of *node*, or ``None`` if it stays hidden."""
        if self._cipher is None or self._share_key is None:
            return None
        encoded, payload = node.get("k"), node.get("a")
        if not isinstance(encoded, str) or not isinstance(payload, str):
            return None
        ciphertext = select_key_ciphertext(encoded, self._share_handle)
        if ciphertext is None:
            return None
        self._attempts += 1
        try:
            raw = decrypt_node_key(self._cipher, self._share_key, ciphertext)
            name = attribute_name(decrypt_attributes(self._cipher, node_aes_key(raw), payload))
        except ProviderCryptoError:
            return None
        self._successes += 1
        return name


def share_url(url: str, link: MegaLink) -> str:
    """Return the canonical share URL of *link*, without its key.

    A legacy link keeps its whole identity in the fragment, so stripping the
    fragment would leave nothing behind. The modern form is rebuilt instead: it
    names the same share, and it carries no secret.
    """
    host = urlsplit(url.strip()).hostname or "mega.nz"
    return f"https://{host}/{link.kind.value}/{link.handle}"


def _handle(node: Mapping[str, Any]) -> str:
    """Return the handle of *node*, or an empty string when it has none."""
    handle = node.get("h")
    return handle if isinstance(handle, str) else ""


def _find_node(nodes: Sequence[Mapping[str, Any]], handle: str) -> Mapping[str, Any] | None:
    """Return the node called *handle*, if the listing contains it."""
    return next((node for node in nodes if _handle(node) == handle), None)


def _find_root(nodes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Return the root of a listing.

    Mega marks it with node type 2; a share whose root is an ordinary folder is
    recognised by having no parent inside the listing.
    """
    if not nodes:
        return None
    marked = next((node for node in nodes if node.get("t") == NODE_ROOT), None)
    if marked is not None:
        return marked
    handles = {_handle(node) for node in nodes}
    orphan = next((node for node in nodes if node.get("p") not in handles), None)
    return orphan if orphan is not None else nodes[0]


def _descendants(nodes: Sequence[Mapping[str, Any]], root_handle: str) -> list[Mapping[str, Any]]:
    """Return every node below *root_handle*, breadth first.

    Traversal follows parent links and remembers what it has seen, so a listing
    that contains a cycle cannot loop.
    """
    by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for node in nodes:
        parent = node.get("p")
        by_parent[parent if isinstance(parent, str) else ""].append(node)
    found: list[Mapping[str, Any]] = []
    seen = {root_handle}
    queue = deque([root_handle])
    while queue:
        for child in by_parent.get(queue.popleft(), ()):
            handle = _handle(child)
            if handle in seen:
                continue
            seen.add(handle)
            found.append(child)
            queue.append(handle)
    return found


def _entry_order(entry: ResourceEntry) -> tuple[bool, str, str]:
    """Order entries with folders first, then by name, then by handle."""
    return (
        entry.metadata.kind is not ResourceKind.FOLDER,
        (entry.metadata.name or "").casefold(),
        entry.ref.resource_id,
    )
