// Export the same artwork used by the page for Markdown and direct viewing.
// Run from any directory: node ops/render_methodology_figures.mjs
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { figureNames, methodologyFigure } from '../src/fxdash/web/static/methodology-figures.js';

const assets = new URL('../src/fxdash/web/static/', import.meta.url);
const out = new URL('../src/fxdash/web/static/figures/', import.meta.url);
const check = process.argv.includes('--check');
const escape = s => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

// An SVG used as a Markdown image cannot fetch a web font or inherit the page's
// CSS. Embed the same font files and stacks so it also works as a downloaded image.
let fonts = await readFile(new URL('fonts.css', assets), 'utf8');
for (const match of [...fonts.matchAll(/url\("([^"]+)"\)/g)]) {
  const bytes = await readFile(new URL(match[1], assets));
  fonts = fonts.replace(match[0], `url("data:font/woff2;base64,${bytes.toString('base64')}")`);
}
// Image contexts paint a static result, so do not permit a fallback-only frame.
fonts = fonts.replaceAll('font-display: swap;', 'font-display: block;');
const siteCss = await readFile(new URL('style.css', assets), 'utf8');
const stacks = ['display', 'mono'].map(name => {
  const value = siteCss.match(new RegExp(`--${name}:\\s*([^;]+);`))?.[1];
  if (!value) throw new Error(`Missing website font stack: --${name}`);
  return `--${name}: ${value};`;
}).join(' ');
const licenses = await Promise.all(['Outfit', 'IBM-Plex-Mono'].map(async name =>
  `${name}\n${await readFile(new URL(`fonts/OFL-${name}.txt`, assets), 'utf8')}`));
const embedded = `<metadata>${escape(licenses.join('\n\n'))}</metadata>\n    <style>${fonts}\n.method-diagram { ${stacks} }</style>`;

if (!check) await mkdir(out, { recursive: true });
for (const lang of ['en', 'zh']) {
  for (const name of figureNames) {
    const svg = methodologyFigure(name, lang).replace('<style>', `${embedded}\n    <style>`)
      .replace(/\r\n/g, '\n').replace(/[ \t]+$/gm, '') + '\n';
    const target = new URL(`${name}-${lang}.svg`, out);
    if (check) {
      const saved = (await readFile(target, 'utf8')).replace(/\r\n/g, '\n');
      if (saved !== svg) throw new Error(`Regenerate ${name}-${lang}.svg`);
    } else {
      await writeFile(target, svg, 'utf8');
    }
  }
}
console.log(`${check ? 'Verified' : 'Exported'} six Methodology illustrations with the website fonts.`);
