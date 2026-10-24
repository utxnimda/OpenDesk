const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../nimda_opendesk/bootstrap.js'), 'utf8');

async function fixture() {
  const requests = [], responses = [];
  class Element {
    constructor() { this.attributes = {}; this.style = {}; }
    setAttribute(name, value) { this.attributes[name] = value; }
    appendChild() {}
  }
  class HTMLImageElement extends Element {}
  class HTMLAnchorElement extends Element {}
  for (const [type, key] of [[HTMLImageElement, 'src'], [HTMLAnchorElement, 'href']]) {
    Object.defineProperty(type.prototype, key, { configurable: true, get() { return this.attributes[key]; }, set(value) { this.attributes[key] = value; } });
  }
  class Storage {
    constructor() { this.values = new Map(); }
    getItem(key) { return this.values.get(key) ?? null; }
    setItem(key, value) { this.values.set(key, String(value)); }
    removeItem(key) { this.values.delete(key); }
  }
  const origin = 'http://127.0.0.1:45678';
  class BrowserRequest extends Request {
    constructor(input, init) { super(typeof input === 'string' ? new URL(input, origin + '/').href : input, init); }
  }
  const context = { console, URL, URLSearchParams, Headers, Request: BrowserRequest, Response, Promise,
    Storage, localStorage: new Storage(), sessionStorage: new Storage(), Element, HTMLImageElement, HTMLAnchorElement,
    location: { origin, href: origin + '/#nimda_session=synthetic-token', hash: '#nimda_session=synthetic-token', pathname: '/', search: '' },
    history: { replaceState() {} }, addEventListener() {}, document: { getElementById() { return null; }, createElement() { return new Element(); }, body: new Element() },
    fetch: async (input, init) => {
      const request = input instanceof Request ? input : new BrowserRequest(input, init);
      requests.push(request);
      const response = new Response('{}', { status: request.url.includes('/failure') ? 409 : 200, headers: { 'Content-Type': 'application/json' } });
      responses.push(response);
      return response;
    } };
  context.window = context;
  vm.runInNewContext(source, context, { filename: 'bootstrap.js' });
  await context.NimdaOpenDeskReady;
  requests.length = 0; responses.length = 0;
  return { context, requests, responses, origin };
}

test('same-origin API fetch authenticates Request objects without changing failure responses', async () => {
  const f = await fixture();
  const response = await f.context.fetch(new f.context.Request('/api/failure', { method: 'POST', body: 'payload', headers: { 'X-Test': 'preserved' } }));
  assert.equal(f.requests[0].headers.get('X-Nimda-Session'), 'synthetic-token');
  assert.equal(f.requests[0].headers.get('X-Test'), 'preserved');
  assert.equal(await f.requests[0].text(), 'payload');
  assert.equal(response, f.responses[0]);
  assert.equal(response.status, 409);
});

test('FormData preserves multipart content and browser-generated boundary', async () => {
  const f = await fixture(), form = new FormData();
  form.append('file', new Blob(['synthetic yaml']), 'synthetic.yaml');
  await f.context.fetch('/api/browse', { method: 'POST', body: form });
  assert.match(f.requests[0].headers.get('Content-Type'), /^multipart\/form-data; boundary=/);
  const parsed = await f.requests[0].formData();
  assert.equal(parsed.get('file').name, 'synthetic.yaml');
  assert.equal(await parsed.get('file').text(), 'synthetic yaml');
});

test('external HTTP and similar-looking authorities never receive the session', async () => {
  const f = await fixture();
  for (const url of ['https://example.com/api/read', 'http://127.0.0.1:45679/api/read', 'http://127.0.0.1.evil.example:45678/api/read']) {
    await f.context.fetch(url, { headers: { 'X-Test': 'kept' } });
    assert.equal(f.requests.at(-1).headers.get('X-Nimda-Session'), null);
    assert.equal(f.requests.at(-1).headers.get('X-Test'), 'kept');
  }
});

test('private img src and log download href are authenticated before assignment', async () => {
  const f = await fixture();
  const img = new f.context.HTMLImageElement(), anchor = new f.context.HTMLAnchorElement();
  img.src = '/api/catalog-library/people/assets/a.webp';
  assert.equal(new URL(img.src).searchParams.get('__nimda_session'), 'synthetic-token');
  anchor.href = '/api/operations/synthetic/log/file?download=1';
  assert.equal(new URL(anchor.href).searchParams.get('download'), '1');
  assert.equal(new URL(anchor.href).searchParams.get('__nimda_session'), 'synthetic-token');
  img.setAttribute('src', '/api/catalog-library/assets/b.jpg');
  assert.equal(new URL(img.attributes.src).searchParams.get('__nimda_session'), 'synthetic-token');
  anchor.setAttribute('href', '/api/operations/synthetic/log/file?download=1');
  assert.equal(new URL(anchor.attributes.href).searchParams.get('__nimda_session'), 'synthetic-token');
});

test('external links and arbitrary API links are unchanged', async () => {
  const f = await fixture();
  for (const value of ['https://example.com/api/catalog-library/assets/a.jpg', '/api/write', '/features/catalog-library/tmdb-logo.svg']) {
    const anchor = new f.context.HTMLAnchorElement(); anchor.href = value;
    assert.equal(anchor.href, value);
  }
});

test('preferences share only application keys and never serialize the session token', async () => {
  const f = await fixture();
  f.context.sessionStorage.setItem('another-session-key', 'private');
  f.context.localStorage.setItem('unrelated', 'private');
  assert.equal(f.requests.length, 0);
  f.context.localStorage.setItem('jp-tv-browse-theme', 'sakura');
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(f.requests.length, 1);
  assert.deepEqual(await f.requests[0].json(), { key: 'jp-tv-browse-theme', value: 'sakura' });
});
