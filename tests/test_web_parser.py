"""Tests for the HTML link parser."""

import pytest

from maxicrawler.web import LinkKind, ParsedHtml
from maxicrawler.web.parser import DEFAULT_MAX_LINKS, HtmlLinkParser, HtmlParser


def parse(markup: str, **kwargs: int) -> ParsedHtml:
    """Return the declarations of *markup*."""
    return HtmlLinkParser(**kwargs).parse(markup)


def targets(markup: str) -> list[str]:
    """Return every link target in *markup*, in document order."""
    return [link.value for link in parse(markup).raw_links]


def meta_refresh(content: str) -> str:
    """Return a meta refresh element carrying *content* verbatim.

    The attribute is quoted with whichever character *content* does not use,
    so a value holding double quotes does not silently end the attribute and
    turn a parser test into a test of the test.
    """
    quote = "'" if '"' in content else '"'
    return f"<meta http-equiv='refresh' content={quote}{content}{quote}>"


def test_the_parser_satisfies_the_runtime_protocol() -> None:
    assert isinstance(HtmlLinkParser(), HtmlParser)


# --- the element table -------------------------------------------------------


@pytest.mark.parametrize(
    ("markup", "kind", "tag", "attribute"),
    [
        ('<a href="/a">x</a>', LinkKind.ANCHOR, "a", "href"),
        ('<area href="/a">', LinkKind.ANCHOR, "area", "href"),
        ('<img src="/i.png">', LinkKind.IMAGE, "img", "src"),
        ('<script src="/s.js"></script>', LinkKind.SCRIPT, "script", "src"),
        ('<link href="/s.css" rel="stylesheet">', LinkKind.STYLESHEET, "link", "href"),
        ('<iframe src="/f"></iframe>', LinkKind.FRAME, "iframe", "src"),
    ],
)
def test_every_supported_element_yields_its_link(
    markup: str, kind: LinkKind, tag: str, attribute: str
) -> None:
    (link,) = parse(markup).raw_links

    assert link.kind is kind
    assert link.tag == tag
    assert link.attribute == attribute


def test_links_keep_their_document_order() -> None:
    markup = '<img src="/1"><a href="/2">x</a><script src="/3"></script>'

    assert targets(markup) == ["/1", "/2", "/3"]


def test_a_self_closing_element_still_yields_its_link() -> None:
    assert targets('<img src="/i.png" />') == ["/i.png"]


def test_an_unlisted_attribute_is_ignored() -> None:
    assert targets('<a name="top" id="x">no href</a>') == []


def test_an_empty_href_is_ignored() -> None:
    assert targets('<a href="">x</a>') == []


def test_a_whitespace_only_href_is_ignored() -> None:
    assert targets('<a href="   ">x</a>') == []


def test_an_href_wrapped_across_lines_is_joined() -> None:
    assert targets('<a href="/very\n  /long">x</a>') == ["/very  /long"]


def test_a_character_reference_in_an_href_is_resolved() -> None:
    assert targets('<a href="/s?a=1&amp;b=2">x</a>') == ["/s?a=1&b=2"]


def test_tag_and_attribute_names_are_matched_case_insensitively() -> None:
    assert targets('<A HREF="/a">x</A>') == ["/a"]


# --- meta refresh ------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("5; url=/next", "/next"),
        ("0;URL='/next'", "/next"),
        ('10 ; Url = "/next"', "/next"),
        ("0,url=/next", "/next"),
        ("url=/next", "/next"),
        ("0; url=https://example.test/next", "https://example.test/next"),
    ],
)
def test_a_meta_refresh_target_is_read(content: str, expected: str) -> None:
    (link,) = parse(meta_refresh(content)).raw_links

    assert link.value == expected
    assert link.kind is LinkKind.REDIRECT
    assert link.attribute == "content"


@pytest.mark.parametrize("content", ["5", "", "5; ", "5; target=/next", "nonsense"])
def test_a_meta_refresh_without_a_target_yields_nothing(content: str) -> None:
    assert targets(meta_refresh(content)) == []


def test_a_meta_that_is_not_a_refresh_is_ignored() -> None:
    assert targets('<meta http-equiv="content-type" content="0; url=/next">') == []


def test_a_meta_refresh_is_recognized_whatever_the_case() -> None:
    assert targets('<META HTTP-EQUIV="Refresh" CONTENT="0; URL=/next">') == ["/next"]


# --- base, title, canonical --------------------------------------------------


def test_the_base_href_is_read() -> None:
    assert parse('<base href="/docs/">').base_href == "/docs/"


def test_the_first_base_wins() -> None:
    markup = '<base href="/first/"><base href="/second/">'

    assert parse(markup).base_href == "/first/"


def test_a_base_without_an_href_is_ignored() -> None:
    markup = '<base target="_blank"><base href="/real/">'

    assert parse(markup).base_href == "/real/"


def test_no_base_reports_none() -> None:
    assert parse("<html><body>x</body></html>").base_href is None


def test_the_title_is_read() -> None:
    assert parse("<html><head><title>  Hello  </title></head></html>").title == "Hello"


def test_an_empty_title_reports_none() -> None:
    assert parse("<title></title>").title is None


def test_the_first_title_wins() -> None:
    assert parse("<title>First</title><title>Second</title>").title == "First"


def test_the_canonical_url_is_recorded() -> None:
    markup = '<link rel="canonical" href="https://example.test/real">'

    assert parse(markup).canonical_href == "https://example.test/real"


def test_a_canonical_link_is_also_reported_as_a_link() -> None:
    markup = '<link rel="canonical" href="https://example.test/real">'

    assert targets(markup) == ["https://example.test/real"]


def test_a_canonical_among_several_rel_values_is_found() -> None:
    markup = '<link rel="alternate canonical" href="/real">'

    assert parse(markup).canonical_href == "/real"


def test_a_stylesheet_link_is_not_a_canonical() -> None:
    assert parse('<link rel="stylesheet" href="/s.css">').canonical_href is None


# --- prose -------------------------------------------------------------------


def test_prose_is_collected() -> None:
    parsed = parse("<html><body><p>Visit https://example.test/x</p></body></html>")

    assert "https://example.test/x" in parsed.text


def test_script_and_style_content_is_not_prose() -> None:
    markup = (
        "<script>var u = 'https://evil.test/x';</script>"
        "<style>body { background: url(https://evil.test/y); }</style>"
        "<p>real</p>"
    )
    parsed = parse(markup)

    assert "evil.test" not in parsed.text
    assert "real" in parsed.text


def test_a_script_src_is_still_a_link_although_its_body_is_not_prose() -> None:
    assert targets('<script src="/s.js">var x = 1;</script>') == ["/s.js"]


# --- robustness --------------------------------------------------------------


@pytest.mark.parametrize(
    "markup",
    [
        '<a href="/a">unclosed',
        "<p><div><a href='/a'>badly nested</p></a></div>",
        "<a href=/a>unquoted</a>",
        "</ stray close><a href='/a'>x</a>",
        '<a href="/a" <b>broken attribute</a>',
        "<!-- <a href='/comment'> --><a href='/a'>x</a>",
        "<![CDATA[ junk ]]><a href='/a'>x</a>",
        "<!doctype html><a href='/a'>x</a>",
        "<a href='/a'>x</a><<<>>>",
    ],
)
def test_malformed_markup_never_raises(markup: str) -> None:
    parsed = parse(markup)

    assert isinstance(parsed, ParsedHtml)


def test_malformed_markup_still_yields_the_links_it_could_read() -> None:
    assert "/a" in targets("<p><div><a href='/a'>badly nested</p></a></div>")


def test_an_empty_document_yields_nothing() -> None:
    parsed = parse("")

    assert parsed.raw_links == ()
    assert parsed.title is None
    assert parsed.base_href is None
    assert parsed.truncated is False


def test_a_document_that_is_not_html_at_all_yields_nothing() -> None:
    assert parse("just some words, no markup here").raw_links == ()


# --- the link ceiling --------------------------------------------------------


def test_the_link_count_is_capped() -> None:
    markup = "".join(f'<a href="/{index}">x</a>' for index in range(50))
    parsed = parse(markup, max_links=10)

    assert len(parsed.raw_links) == 10
    assert parsed.truncated is True


def test_a_document_under_the_ceiling_is_not_marked_truncated() -> None:
    parsed = parse('<a href="/a">x</a>', max_links=10)

    assert parsed.truncated is False


def test_the_ceiling_keeps_the_first_links() -> None:
    markup = "".join(f'<a href="/{index}">x</a>' for index in range(50))

    assert targets_of(parse(markup, max_links=3)) == ["/0", "/1", "/2"]


def targets_of(parsed: ParsedHtml) -> list[str]:
    """Return the targets of an already parsed document."""
    return [link.value for link in parsed.raw_links]


def test_a_non_positive_ceiling_is_refused() -> None:
    with pytest.raises(ValueError, match="max_links must be at least 1"):
        HtmlLinkParser(max_links=0)


def test_the_default_ceiling_is_generous() -> None:
    assert DEFAULT_MAX_LINKS >= 1000
