"""Source-level guards for the frontend that the static build depends on.

The same files are served by the live server at the site root and by GitHub Pages
under a project path, so every URL in them must be relative. A root-absolute URL
works on the live server and silently 404s on Pages, which is why this is pinned
here rather than discovered on the published site.
"""

import pathlib
import re

import fxdash.web.app as web_app

STATIC = pathlib.Path(web_app.STATIC_DIR)
OWN_FILES = ["index.html", "app.js", "charts.js", "i18n.js", "methodology.js",
             "methodology-figures.js", "style.css"]

# a URL-bearing attribute, import, fetch or assignment whose value starts with "/"
ROOT_URL = re.compile(r'(?:from |src=|href=|fetch\(|\.src\s*=\s*|url\()\s*["\']/(?!/)')


def _read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_no_root_absolute_urls_in_own_sources():
    offenders = []
    for name in OWN_FILES:
        for i, line in enumerate(_read(name).splitlines(), 1):
            if ROOT_URL.search(line):
                offenders.append(f"{name}:{i}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_index_declares_the_two_header_chips_and_the_footer():
    html = _read("index.html")
    assert 'id="pulse"' in html and 'id="built"' in html
    assert '<footer class="foot" id="foot">' in html
    assert 'src="app.js"' in html and 'src="vendor/echarts.min.js"' in html


def test_headlines_are_never_labelled_live():
    """2026-09-04 ruling: headlines are a snapshot taken at fetched_at, in both
    modes; the word LIVE has no place in the dictionary or the markup."""
    i18n = _read("i18n.js")
    assert '"news.live"' not in i18n
    assert '"news.livenote"' not in i18n
    assert "{time}" in i18n.split('"news.today.title"')[1].split("\n")[0]
    assert "tag--live" not in _read("app.js")
    assert "tag--live" not in _read("style.css")


def test_footer_disclaimer_is_bilingual_and_complete():
    i18n = _read("i18n.js")
    body = i18n.split('"foot.body"')[1].split("},")[0]
    for phrase in ("not investment advice", "language model", "once per evening",
                   "不构成投资建议", "语言模型", "每晚更新一次"):
        assert phrase in body, phrase
    assert '"foot.methodology"' in i18n


def test_source_tree_ships_no_build_artifact():
    """build.json marks a static build; its presence in the source tree would make
    the live server think it is a static site."""
    assert not (STATIC / "build.json").exists()
