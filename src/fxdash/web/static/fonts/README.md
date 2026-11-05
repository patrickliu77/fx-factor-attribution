# Dashboard fonts

The interface and Methodology illustrations use Manrope for text and Geist Mono
for numbers and charts. These unmodified variable WOFF2 Latin files were downloaded
from Google Fonts on 2026-11-04. The source files contain weights 200 to 800 and
100 to 900 respectively; the interface declarations in `../fonts.css` cap both at
700. There are no runtime requests to a font CDN. Chinese text and glyphs outside
these files use the system fallbacks in `../style.css`.

Outfit and IBM Plex Mono remain available for existing standalone research and
operations reports. Their original files and declarations are retained. These
files were downloaded on 2026-09-05; Outfit is variable and IBM Plex Mono includes
weights 400, 500 and 600.

| File | Google Fonts delivery version and source |
|---|---|
| manrope-latin.woff2 | [Manrope v23](https://fonts.gstatic.com/s/manrope/v23/xn7gYHE41ni1AdIRggexSg.woff2) |
| geist-mono-latin.woff2 | [Geist Mono v6](https://fonts.gstatic.com/s/geistmono/v6/or3nQ6H-1_WfwkMZI_qYFrcdmg.woff2) |
| outfit-latin.woff2 | [Outfit v15](https://fonts.gstatic.com/s/outfit/v15/QGYvz_MVcBeNP4NJtEtq.woff2) |
| ibm-plex-mono-latin-400.woff2 | [IBM Plex Mono v20, regular](https://fonts.gstatic.com/s/ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1i8q1w.woff2) |
| ibm-plex-mono-latin-500.woff2 | [IBM Plex Mono v20, medium](https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgg.woff2) |
| ibm-plex-mono-latin-600.woff2 | [IBM Plex Mono v20, semibold](https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3vAOwlBFgg.woff2) |

All four families use the SIL Open Font License 1.1. The original notices are
included alongside the font files:

| Notice | Source |
|---|---|
| [OFL-Manrope.txt](OFL-Manrope.txt) | [Google Fonts Manrope, revision b31870a](https://github.com/google/fonts/blob/b31870aff700ab7a1d74fa0c6887d95beb9e0037/ofl/manrope/OFL.txt) |
| [OFL-Geist-Mono.txt](OFL-Geist-Mono.txt) | [Google Fonts Geist Mono, revision 9e25e2b](https://github.com/google/fonts/blob/9e25e2ba265e5298f70f6182dd4e8a3ebf1b9123/ofl/geistmono/OFL.txt) |
| [OFL-Outfit.txt](OFL-Outfit.txt) | [Google Fonts Outfit](https://github.com/google/fonts/tree/main/ofl/outfit) |
| [OFL-IBM-Plex-Mono.txt](OFL-IBM-Plex-Mono.txt) | [Google Fonts IBM Plex Mono](https://github.com/google/fonts/tree/main/ofl/ibmplexmono) |

The SVG export script embeds all six font files and all four notices in each
standalone illustration. It reads `../fonts.css` and the website font stacks at
export time, keeping Markdown images and downloaded SVGs independent of font
services.

From the repository root:

```sh
node ops/render_methodology_figures.mjs
node ops/render_methodology_figures.mjs --check
```
