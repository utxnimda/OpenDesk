const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Use the OpenDesk root's locked TypeScript dependency, including when this test
// is invoked from another working directory.
const hostRoot = process.env.OPENDESK_TEST_ROOT || process.argv[2];
let typescript;
try {
  typescript = require(require.resolve('typescript', {
    paths: hostRoot ? [path.resolve(hostRoot)] : [__dirname, process.cwd()],
  }));
} catch (_) {}
const available = { skip: typescript ? false : 'Set OPENDESK_TEST_ROOT to an OpenDesk checkout with TypeScript installed.' };
const source = fs.readFileSync(path.join(__dirname, '../frontend/main.ts'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

async function fixture(configured = false) {
  const elements = new Map(), timers = new Map(), calls = [], frames = [];
  let nextTimer = 0, configureFailure = null, presentationFailure = null;
  let session = { url: 'http://127.0.0.1:45678/#nimda_session=synthetic', mode: 'embedded', configured };
  class Element {
    constructor(tag) {
      this.tagName = tag;
      this.hidden = false;
      this.disabled = false;
      this.value = '';
      this.textContent = '';
      this.children = [];
      this.attributes = {};
      this.listeners = new Map();
      this.navigations = 0;
      this.contentDocument = { unsavedInput: '' };
    }
    set src(value) { this.source = value; this.navigations++; }
    get src() { return this.source; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) {
      const listeners = this.listeners.get(name) || [];
      listeners.push(callback);
      this.listeners.set(name, listeners);
    }
    appendChild(child) { this.children.push(child); }
    async dispatch(name) {
      for (const callback of this.listeners.get(name) || []) await callback({ preventDefault() {} });
      await settle();
    }
  }
  const bridge = {
    ready: Promise.resolve(),
    dispose() {},
    host: {
      async callWorker(method, params) {
        calls.push({ method, params });
        if (method === 'nimda.configure') {
          if (configureFailure) throw new Error(configureFailure);
          session = { ...session, configured: true, workspace: params.workspace };
        } else if (method === 'nimda.presentation') {
          if (presentationFailure) throw new Error(presentationFailure);
          session = { ...session, mode: params.mode };
        } else if (method !== 'nimda.status') {
          throw new Error('Unexpected worker method: ' + method);
        }
        return { ...session };
      },
    },
  };
  const context = {
    exports: {}, URL, console,
    require(name) {
      if (name === '@opendesk/module-sdk') return { createModuleBridge: () => bridge, errorMessage: error => error.message };
      if (name === './style.css') return {};
      throw new Error('Unexpected wrapper import: ' + name);
    },
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, new Element(id));
        return elements.get(id);
      },
      createElement(tag) {
        const element = new Element(tag);
        if (tag === 'iframe') frames.push(element);
        return element;
      },
    },
    window: { addEventListener() {} },
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const compiled = typescript.transpileModule(source, {
    compilerOptions: { target: typescript.ScriptTarget.ES2022, module: typescript.ModuleKind.CommonJS },
    fileName: 'wrapper.ts',
  });
  vm.runInNewContext(compiled.outputText, context, { filename: 'wrapper.js' });
  await settle();
  return {
    elements, calls, frames,
    failConfiguration(message) { configureFailure = message; },
    failPresentation(message) { presentationFailure = message; },
    async poll() {
      assert.equal(timers.size, 1, 'the wrapper must own one status polling timer');
      const [id, callback] = timers.entries().next().value;
      timers.delete(id);
      await callback();
      await settle();
    },
  };
}

test('configuration errors survive successful status polling until the user edits or retries', available, async () => {
  const f = await fixture();
  const workspace = f.elements.get('workspace'), error = f.elements.get('error');
  workspace.value = 'C:/synthetic-workspace';
  f.failConfiguration('Choose an existing Nimda workspace.');
  await f.elements.get('configuration').dispatch('submit');
  assert.equal(error.textContent, 'Choose an existing Nimda workspace.');
  for (let index = 0; index < 3; index++) await f.poll();
  assert.equal(f.calls.filter(call => call.method === 'nimda.status').length, 4);
  assert.equal(error.hidden, false);
  assert.equal(error.textContent, 'Choose an existing Nimda workspace.');
  assert.equal(f.frames.length, 0);

  await workspace.dispatch('input');
  assert.equal(error.hidden, true);
  await f.elements.get('configuration').dispatch('submit');
  await f.poll();
  assert.equal(error.hidden, false);
  f.failConfiguration(null);
  await f.elements.get('configuration').dispatch('submit');
  await f.poll();
  assert.equal(error.textContent, '');
  assert.equal(error.hidden, true);
  assert.equal(f.frames.length, 1);
});

test('presentation errors survive status polling and a successful retry clears them', available, async () => {
  const f = await fixture(true), error = f.elements.get('error');
  f.failPresentation('The desktop window is still starting.');
  await f.elements.get('popup').dispatch('click');
  await f.poll();
  assert.equal(error.hidden, false);
  assert.equal(error.textContent, 'The desktop window is still starting.');
  assert.equal(f.elements.get('viewport').hidden, false);
  f.failPresentation(null);
  await f.elements.get('popup').dispatch('click');
  await f.poll();
  assert.equal(error.hidden, true);
  assert.equal(f.elements.get('detached').hidden, false);
});

test('popup and embedded transitions retain one iframe, its document and unsaved input', available, async () => {
  const f = await fixture(true), viewport = f.elements.get('viewport');
  const frame = f.frames[0], document = frame.contentDocument;
  document.unsavedInput = 'Unsaved synthetic draft';
  for (const mode of ['popup', 'embedded', 'popup']) {
    await f.elements.get(mode).dispatch('click');
    await f.poll();
    assert.equal(viewport.hidden, mode === 'popup');
    assert.equal(f.elements.get('detached').hidden, mode !== 'popup');
    assert.equal(f.frames.length, 1);
    assert.equal(viewport.children.length, 1);
    assert.equal(viewport.children[0], frame);
    assert.equal(frame.contentDocument, document);
    assert.equal(frame.navigations, 1);
    assert.equal(document.unsavedInput, 'Unsaved synthetic draft');
  }
  await f.elements.get('return').dispatch('click');
  assert.equal(viewport.hidden, false);
  assert.equal(viewport.children[0], frame);
  assert.equal(frame.navigations, 1);
});
