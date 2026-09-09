// Serve a generated static tree inside a browser test, with no listening socket.
import {readFile} from 'node:fs/promises';
import path from 'node:path';
export async function staticFixture(context,base) {
  const folder=process.env.FXDASH_STATIC_FIXTURE;
  if (!folder) return;
  const root=path.resolve(folder),prefix=new URL(base.replace(/\/$/,'')+'/');
  await context.route(prefix.href+'**',async route=>{
    const url=new URL(route.request().url());
    let relative=decodeURIComponent(url.pathname.slice(prefix.pathname.length));
    if (!relative || relative.endsWith('/')) relative+='index.html';
    const file=path.resolve(root,relative);
    if (!file.startsWith(root+path.sep)) return route.fulfill({status:403,body:''});
    const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8',
      '.json':'application/json','.css':'text/css','.woff2':'font/woff2','.svg':'image/svg+xml'};
    try {await route.fulfill({body:await readFile(file),contentType:types[path.extname(file)] || 'application/octet-stream'});}
    catch {await route.fulfill({status:404,body:''});}
  });
}
