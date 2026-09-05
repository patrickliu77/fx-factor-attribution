"""The embedded Markdown illustrations retain the website's local typefaces."""

import base64
import re
import xml.etree.ElementTree as ET

import pytest

from fxdash.web.app import STATIC_DIR


def test_website_fonts_are_local_and_licensed():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "fonts.css").read_text(encoding="utf-8")
    assert 'href="fonts.css"' in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    fonts = re.findall(r'url\("([^"]+)"\)', css)
    assert len(fonts) == 4
    for font in fonts:
        assert (STATIC_DIR / font).read_bytes().startswith(b"wOF2")
    for family in ("Outfit", "IBM-Plex-Mono"):
        license_text = (STATIC_DIR / "fonts" / f"OFL-{family}.txt").read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE" in license_text


@pytest.mark.parametrize("name", ["pipeline", "timeline", "lasso"])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_standalone_figures_embed_the_website_fonts_and_fallbacks(name, lang):
    svg = (STATIC_DIR / "figures" / f"{name}-{lang}.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    styles = "\n".join(node.text or "" for node in root.findall("svg:style", ns))
    assert root.find("svg:title", ns).text
    assert root.find("svg:desc", ns).text
    assert "SIL OPEN FONT LICENSE" in root.find("svg:metadata", ns).text
    assert not root.findall(".//svg:script", ns)
    assert "@import" not in styles
    encoded = re.findall(r'data:font/woff2;base64,([A-Za-z0-9+/=]+)', styles)
    expected = {font.read_bytes() for font in (STATIC_DIR / "fonts").glob("*.woff2")}
    assert {base64.b64decode(font, validate=True) for font in encoded} == expected
    assert len(encoded) == len(expected)
    assert all(url.startswith("data:") for url in re.findall(r'url\("([^"]+)"\)', styles))
    site_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    for token in ("display", "mono"):
        declaration = re.search(rf"--{token}:\s*([^;]+);", site_css).group(0)
        assert declaration in styles


def test_readme_links_to_generated_figures():
    readme = (STATIC_DIR.parents[3] / "README.md").read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", readme)
    assert len(images) >= 2
    for image in images:
        assert (STATIC_DIR.parents[3] / image).is_file()
