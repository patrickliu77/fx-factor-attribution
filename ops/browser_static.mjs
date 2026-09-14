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
      '.json':'application/json','.css':'text/css','.woff2':'font/woff2','.svg':'image/svg+xml','.mp3':'audio/mpeg'};
    try {
      const body=await readFile(file),contentType=types[path.extname(file)] || 'application/octet-stream';
      if (path.extname(file)==='.mp3') {
        const headers={'Accept-Ranges':'bytes'},range=route.request().headers().range;
        if (range) {
          const match=/^bytes=(\d*)-(\d*)$/.exec(range);
          const start=match?.[1] ? Number(match[1]) : Math.max(0,body.length-Number(match?.[2]));
          const end=match?.[1] && match?.[2] ? Math.min(Number(match[2]),body.length-1) : body.length-1;
          if (!match || !(match[1] || match[2]) || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)
              || start<0 || start>=body.length || end<start) {
            return route.fulfill({status:416,headers:{...headers,'Content-Range':`bytes */${body.length}`},body:''});
          }
          return route.fulfill({status:206,contentType,headers:{...headers,'Content-Range':`bytes ${start}-${end}/${body.length}`},body:body.subarray(start,end+1)});
        }
        return route.fulfill({body,contentType,headers});
      }
      await route.fulfill({body,contentType});
    }
    catch {await route.fulfill({status:404,body:''});}
  });
}
