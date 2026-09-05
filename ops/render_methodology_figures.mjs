// Export the same artwork used by the page for Markdown and direct viewing.
// Run from any directory: node ops/render_methodology_figures.mjs
import { mkdir, writeFile } from 'node:fs/promises';
import { figureNames, methodologyFigure } from '../src/fxdash/web/static/methodology-figures.js';

const out = new URL('../src/fxdash/web/static/figures/', import.meta.url);
await mkdir(out, { recursive: true });
for (const lang of ['en', 'zh']) {
  for (const name of figureNames) {
    const svg = methodologyFigure(name, lang).replace(/[ \t]+$/gm, '') + '\n';
    await writeFile(new URL(`${name}-${lang}.svg`, out), svg, 'utf8');
  }
}
console.log('Exported six Methodology illustrations.');
