# Dashboard fonts

The website and Methodology illustrations share Outfit and IBM Plex Mono.
These are unmodified WOFF2 Latin files distributed by Google Fonts, downloaded
on 2026-09-05. Outfit is variable; IBM Plex Mono includes weights 400, 500 and 600.
Chinese text and glyphs outside these files use the stacks in `../style.css`.

| File | Source |
|---|---|
| outfit-latin.woff2 | [Outfit v15](https://fonts.gstatic.com/s/outfit/v15/QGYvz_MVcBeNP4NJtEtq.woff2) |
| ibm-plex-mono-latin-400.woff2 | [IBM Plex Mono v20, regular](https://fonts.gstatic.com/s/ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1i8q1w.woff2) |
| ibm-plex-mono-latin-500.woff2 | [IBM Plex Mono v20, medium](https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgg.woff2) |
| ibm-plex-mono-latin-600.woff2 | [IBM Plex Mono v20, semibold](https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3vAOwlBFgg.woff2) |

Both families use the SIL Open Font License 1.1. Original notices are included in
[OFL-Outfit.txt](OFL-Outfit.txt) and [OFL-IBM-Plex-Mono.txt](OFL-IBM-Plex-Mono.txt),
copied from the respective [Outfit](https://github.com/google/fonts/tree/main/ofl/outfit)
and [IBM Plex Mono](https://github.com/google/fonts/tree/main/ofl/ibmplexmono) directories.
The SVG export script embeds the font files and these notices in each standalone
illustration. It reads `../fonts.css` and the website font stacks at export time.

From the repository root:

```sh
node ops/render_methodology_figures.mjs
node ops/render_methodology_figures.mjs --check
```
