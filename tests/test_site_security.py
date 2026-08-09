"""Guard the site's XSS defences.

site/index.html renders JSON written by an LLM, so entry text and URLs are
untrusted input. These checks pin the invariants that keep that safe. They are
static (no browser needed) and fail loudly if someone reintroduces raw
interpolation into an href.

Run:  python -m tests.test_site_security
"""
import os
import re

SITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "site", "index.html")

HTML = open(SITE).read()


def test_no_raw_url_in_href():
    # The original bug: `href="${u}"` let a crafted URL break out of the
    # attribute or smuggle in a javascript: payload.
    assert 'href="${u}"' not in HTML
    assert re.search(r'href="\$\{esc\(parsed\.href\)\}"', HTML), \
        "hrefs must be built from an escaped, parsed URL"


def test_url_scheme_allowlist():
    assert 'parsed.protocol !== "http:"' in HTML
    assert 'parsed.protocol !== "https:"' in HTML


def test_external_links_have_rel():
    for m in re.finditer(r'target="_blank"(.{0,60})', HTML, re.S):
        assert "noopener" in m.group(1), \
            "target=_blank needs rel=noopener noreferrer (reverse tabnabbing)"


def test_escape_covers_attribute_breakers():
    body = re.search(r"const ESC = \{(.*?)\};", HTML, re.S).group(1)
    for ch in ("&", "<", ">", '"', "'"):
        assert ch in body, f"esc() must escape {ch!r}"


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("\nRESULT: PASS")


if __name__ == "__main__":
    main()
