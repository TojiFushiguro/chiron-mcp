// Ask's PDF reports, shown by Open (see Reports in index.html).
//
// A PDF made in the page has only a blob: address, and a browser's PDF viewer saves a file
// under the last part of the address it shows, which for a blob: address is a random id.
// So Open hands the PDF to this worker and shows it at files/<id>/<title>.pdf instead:
// saved from the viewer, the file keeps the report's title.
//
// The worker answers only for files/ (its scope), keeps the last few PDFs in memory, sends
// nothing anywhere and writes nothing to disk. A worker the browser has stopped has
// forgotten its PDFs, and the address then says to press Open again.
const FILES = new Map();
const KEEP = 12;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('message', event => {
  const d = event.data || {};
  if (d.type !== 'pdf' || typeof d.path !== 'string' || !(d.blob instanceof Blob)) return;
  FILES.delete(d.path);
  FILES.set(d.path, d.blob);
  while (FILES.size > KEEP) FILES.delete(FILES.keys().next().value);
  if (event.ports && event.ports[0]) event.ports[0].postMessage('ok');
});

const GONE = '<!doctype html><html lang="en"><meta charset="utf-8"><title>PDF report</title>'
  + '<p style="font: 15px/1.5 system-ui, sans-serif; color: #6b7780; padding: 24px">'
  + 'This PDF is no longer open here. Go back to Ask and press Open on the report again.</p></html>';

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const blob = FILES.get(url.pathname);
  if (!blob) {
    event.respondWith(new Response(GONE, { status: 404, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } }));
    return;
  }
  let name = 'Report.pdf';
  try { name = decodeURIComponent(url.pathname.split('/').pop()) || name; } catch (_) { /* keep the plain name */ }
  event.respondWith(new Response(blob, { headers: {
    'Content-Type': 'application/pdf',
    'Content-Disposition': "inline; filename*=UTF-8''" + encodeURIComponent(name),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  } }));
});
