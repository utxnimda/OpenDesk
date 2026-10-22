/**
 * A deliberately bounded architecture check, not a security scanner.
 * TS/JS and Vue scripts use the TypeScript AST; Python uses stdlib ast.
 * Rust uses tokens (including nested-comment/raw-string handling), not macro expansion.
 * Computed imports are rejected rather than guessed. Aliased globals, dependency
 * internals, generated code, Rust macros and runtime ownership still need review.
 */
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, extname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import ts from 'typescript';
import { parse as parseVue } from 'vue/compiler-sfc';
import { pythonExecutable } from './environment.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ignored = new Set(['node_modules', '.tools', '.cache', 'dist', 'target', '.git', '.venv', '__pycache__']);
const composition = 'apps/framework/frontend/src/core/discovery.ts';
const hostAdapter = 'apps/framework/frontend/src/core/host.ts';
const required = ['AGENTS.md', 'docs/principles.md', 'docs/architecture.md', 'docs/adding-a-module.md'];
const errors = [];
const normalize = (value) => value.replaceAll('\\', '/');
const rel = (path) => normalize(relative(root, path));
const report = (file, line, rule, detail) => errors.push(`${file}:${line} [${rule}] ${detail}`);
const genericExecution = /^(?:(?:shell|process|system|runtime)[._:-])?(?:exec(?:ute)?(?:[._-]?(?:command|shell|process))?|shell|spawn|run[._-]?(?:command|shell|process))$/i;

function filesBelow(path) {
  if (!existsSync(path)) return [];
  return readdirSync(path, { withFileTypes: true }).flatMap((entry) => {
    if (ignored.has(entry.name) || entry.isSymbolicLink()) return [];
    const child = resolve(path, entry.name);
    return entry.isDirectory() ? filesBelow(child) : [child];
  });
}

function featureOwner(path) {
  const parts = normalize(path).replace(/^\//, '').split('/');
  const index = parts.indexOf('features');
  if (index < 0 || !parts[index + 1]) return undefined;
  const next = parts[index + 1];
  return next === '_examples' ? `_examples/${parts[index + 2] ?? ''}` : next;
}

function sourceTarget(file, specifier) {
  // Current project intentionally has no TS/Vite path aliases. A new alias must
  // extend this resolver; unresolved aliases pointing at features are rejected.
  if (specifier.startsWith('.')) return rel(resolve(root, dirname(file), specifier));
  return normalize(specifier).replace(/^[@/~]+/, '');
}

function dependency(file, line, specifier, frontend = false) {
  const target = sourceTarget(file, specifier);
  const owner = featureOwner(target);
  if (file.startsWith('apps/framework/') && owner && file !== composition) {
    report(file, line, 'framework-dependency', `Framework cannot import a feature: ${specifier}`);
  }
  const current = featureOwner(file);
  if (current && owner && current !== owner) {
    report(file, line, 'feature-dependency', `Feature ${current} cannot import sibling ${owner}. Use an explicit framework contract.`);
  }
  if (frontend && (specifier === '@tauri-apps/api' || specifier.startsWith('@tauri-apps/api/')) && file !== hostAdapter) {
    report(file, line, 'host-adapter', 'Import Tauri APIs only from core/host.ts.');
  }
  if (frontend && (/^@tauri-apps\/plugin-shell(?:\/|$)/.test(specifier) || /^(?:node:)?child_process$/.test(specifier))) {
    report(file, line, 'execution-capability', 'Frontend cannot import a general process/shell execution bridge.');
  }
  if (frontend && /^@tauri-apps\//.test(specifier) && !specifier.startsWith('@tauri-apps/api') && file !== hostAdapter) {
    report(file, line, 'host-adapter', 'Desktop plugin access belongs in the host adapter with a named contract.');
  }
}

function unwrap(node) {
  while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node) || ts.isTypeAssertionExpression(node))) node = node.expression;
  return node;
}

function memberRoot(node) {
  node = unwrap(node);
  while (node && (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node))) node = unwrap(node.expression);
  return node && ts.isIdentifier(node) ? node.text : undefined;
}

function memberName(node) {
  node = unwrap(node);
  if (node && ts.isIdentifier(node)) return node.text;
  if (node && ts.isPropertyAccessExpression(node)) return node.name.text;
  if (node && ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) return node.argumentExpression.text;
  return undefined;
}

function isGlobal(node) { return ['window', 'globalThis'].includes(memberRoot(node)); }
function literalStrings(node) {
  if (!node) return undefined;
  if (ts.isStringLiteralLike(node)) return [node.text];
  if (ts.isArrayLiteralExpression(node)) {
    const values = node.elements.map(literalStrings);
    return values.every(Boolean) ? values.flat() : undefined;
  }
  return undefined;
}

function inspectScript(file, text, offset = 0) {
  const ast = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const line = (node) => offset + ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1;
  for (const issue of ast.parseDiagnostics) report(file, offset + ast.getLineAndCharacterOfPosition(issue.start ?? 0).line + 1, 'parse', ts.flattenDiagnosticMessageText(issue.messageText, ' '));
  const addImport = (node, argument) => {
    const values = literalStrings(argument);
    if (!values) report(file, line(node), 'static-import', 'Use a literal import/glob path so module boundaries can be checked.');
    else for (const value of values) dependency(file, line(node), value, true);
  };
  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) addImport(node, node.moduleSpecifier);
    if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) addImport(node, node.moduleReference.expression);
    if (ts.isCallExpression(node)) {
      const callee = unwrap(node.expression);
      const name = memberName(callee);
      const isImport = callee.kind === ts.SyntaxKind.ImportKeyword;
      const isRequire = ts.isIdentifier(callee) && callee.text === 'require';
      const isGlob = ts.isPropertyAccessExpression(callee) && ['glob', 'globEager'].includes(callee.name.text) && ts.isMetaProperty(callee.expression) && callee.expression.keywordToken === ts.SyntaxKind.ImportKeyword;
      if (isImport || isRequire || isGlob) addImport(node, node.arguments[0]);
      if (['invoke', 'request', 'callWorker'].includes(name) && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0]) && genericExecution.test(node.arguments[0].text)) {
        report(file, line(node), 'execution-capability', 'Expose a specific validated operation instead of a general shell/exec command.');
      }
      if (['assign', 'defineProperty', 'defineProperties'].includes(name) && memberRoot(callee) === 'Object' && isGlobal(node.arguments[0])) {
        report(file, line(node), 'global-registry', 'Do not publish application state or registries on window/globalThis.');
      }
      if ((name === 'eval' && ts.isIdentifier(callee)) || name === 'Function' && ts.isIdentifier(callee)) {
        report(file, line(node), 'dynamic-code', 'Dynamic code execution is outside the module contract.');
      }
    }
    if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'Function') report(file, line(node), 'dynamic-code', 'Dynamic code execution is outside the module contract.');
    if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment && isGlobal(node.left)) {
      report(file, line(node), 'global-registry', 'Do not assign application state or registries to window/globalThis.');
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
}

function inspectFrontend(file, text) {
  if (!file.endsWith('.vue')) return inspectScript(file, text);
  const parsed = parseVue(text, { filename: file });
  for (const error of parsed.errors) report(file, error.loc?.start.line ?? 1, 'vue-parse', typeof error === 'string' ? error : error.message);
  for (const block of [parsed.descriptor.script, parsed.descriptor.scriptSetup].filter(Boolean)) {
    if (block.src) dependency(file, block.loc.start.line, block.src, true);
    else inspectScript(file, block.content, block.loc.start.line - 1);
  }
}

// This tokenizer strips Rust comments as syntax, preserving actual string tokens
// for #[path] and include!. Raw strings and nested block comments are supported.
function rustTokens(text) {
  const tokens = [];
  let at = 0;
  let line = 1;
  const advance = (end) => { line += (text.slice(at, end).match(/\n/g) ?? []).length; at = end; };
  while (at < text.length) {
    if (/\s/.test(text[at])) { advance(at + 1); continue; }
    if (text.startsWith('//', at)) { const end = text.indexOf('\n', at); advance(end < 0 ? text.length : end); continue; }
    if (text.startsWith('/*', at)) {
      let depth = 1;
      let end = at + 2;
      while (end < text.length && depth) {
        if (text.startsWith('/*', end)) { depth++; end += 2; }
        else if (text.startsWith('*/', end)) { depth--; end += 2; }
        else end++;
      }
      advance(end); continue;
    }
    const raw = /^(?:br|r)(#*)"/.exec(text.slice(at));
    if (raw) {
      const start = at + raw[0].length;
      const marker = '"' + raw[1];
      const end = text.indexOf(marker, start);
      tokens.push({ kind: 'string', value: text.slice(start, end < 0 ? text.length : end), line });
      advance(end < 0 ? text.length : end + marker.length); continue;
    }
    if (text[at] === '"' || text.startsWith('b"', at)) {
      const start = at + (text[at] === 'b' ? 2 : 1);
      let end = start;
      while (end < text.length) {
        if (text[end] === '\\') end += 2;
        else if (text[end++] === '"') break;
      }
      tokens.push({ kind: 'string', value: text.slice(start, end - 1), line });
      advance(end); continue;
    }
    // Rust lifetimes ('a) are identifiers; only complete char literals are skipped.
    const character = /^(?:b)?'(?:\\(?:u\{[\da-fA-F_]+\}|x[\da-fA-F]{2}|.)|[^'\\\n])'/.exec(text.slice(at));
    if (character) { advance(at + character[0].length); continue; }
    const word = /^[A-Za-z_][A-Za-z_\d]*/.exec(text.slice(at));
    if (word) { tokens.push({ kind: 'word', value: word[0], line }); advance(at + word[0].length); continue; }
    tokens.push({ kind: 'punctuation', value: text[at], line });
    advance(at + 1);
  }
  return tokens;
}

function inspectRust(file, text) {
  const tokens = rustTokens(text);
  for (let index = 0; index < tokens.length; index++) {
    const token = tokens[index];
    if (token.kind !== 'word') continue;
    if (token.value === 'use') {
      const end = tokens.findIndex((entry, position) => position > index && entry.value === ';');
      const imported = tokens.slice(index + 1, end < 0 ? tokens.length : end);
      const path = imported.map((entry) => entry.value).join('').replaceAll('::', '/');
      if (file.startsWith('apps/framework/') && imported.some((entry) => entry.kind === 'word' && entry.value === 'features')) {
        report(file, token.line, 'framework-dependency', 'Framework cannot import a Rust features namespace.');
      } else dependency(file, token.line, path);
    }
    if (token.value === 'mod' && tokens[index + 1]?.value === 'features' && file.startsWith('apps/framework/')) {
      report(file, token.line, 'framework-dependency', 'Framework cannot declare a concrete features module.');
    }
    if (token.value === 'path' && tokens[index + 1]?.value === '=' && tokens[index + 2]?.kind === 'string') dependency(file, token.line, tokens[index + 2].value);
    if (token.value === 'include' && tokens[index + 1]?.value === '!' && tokens[index + 3]?.kind === 'string') dependency(file, token.line, tokens[index + 3].value);
    if (token.value === 'command' && tokens[index - 1]?.value === ':' && tokens[index - 2]?.value === ':' && tokens[index - 3]?.value === 'tauri') {
      const nextFn = tokens.slice(index + 1).findIndex((entry) => entry.kind === 'word' && entry.value === 'fn');
      const name = nextFn >= 0 ? tokens[index + nextFn + 2] : undefined;
      if (name && genericExecution.test(name.value)) report(file, name.line, 'execution-capability', 'Tauri cannot expose a generic shell/exec command.');
    }
  }
}

function inspectPython(paths) {
  if (!paths.length) return;
  const script = String.raw`
import ast, json, pathlib, sys
records = []
for filename in json.load(sys.stdin):
    try:
        tree = ast.parse(pathlib.Path(filename).read_text(encoding="utf-8-sig"), filename=filename)
    except (SyntaxError, UnicodeError) as error:
        records.append({"file": filename, "line": getattr(error, "lineno", 1), "kind": "parse", "value": str(error)})
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                records.append({"file": filename, "line": node.lineno, "kind": "import", "value": item.name.replace(".", "/")})
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").replace(".", "/")
            prefix = ("./" if node.level == 1 else "../" * (node.level - 1)) if node.level else ""
            for item in node.names:
                records.append({"file": filename, "line": node.lineno, "kind": "import", "value": prefix + module + "/" + item.name})
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in ("__import__", "import_module", "register"):
                value = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else None
                if name == "register" and value is not None:
                    records.append({"file": filename, "line": node.lineno, "kind": "method", "value": value})
                elif name != "register":
                    records.append({"file": filename, "line": node.lineno, "kind": "import" if value is not None else "computed", "value": (value or "").replace(".", "/")})
json.dump(records, sys.stdout)
`;
  const result = spawnSync(pythonExecutable(), ['-X', 'utf8', '-c', script], { input: JSON.stringify(paths), encoding: 'utf8', windowsHide: true, maxBuffer: 4 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`Python AST check failed: ${result.error?.message ?? result.stderr}`);
  for (const record of JSON.parse(result.stdout)) {
    const file = rel(record.file);
    if (record.kind === 'import') dependency(file, record.line, record.value);
    else if (record.kind === 'computed') report(file, record.line, 'static-import', 'Computed Python imports need an explicit reviewed composition boundary.');
    else if (record.kind === 'method' && genericExecution.test(record.value)) report(file, record.line, 'execution-capability', 'Worker methods cannot expose generic shell/exec.');
    else if (record.kind === 'parse') report(file, record.line, 'python-parse', record.value);
  }
}

function checkMetadata() {
  for (const file of required) if (!existsSync(resolve(root, file)) || !readFileSync(resolve(root, file), 'utf8').trim()) report(file, 1, 'required-document', 'Required engineering document is missing or empty.');
  const defaultsPath = 'config/framework/defaults.json';
  try {
    const value = JSON.parse(readFileSync(resolve(root, defaultsPath), 'utf8'));
    if (!value || Array.isArray(value) || Object.keys(value).sort().join(',') !== 'closeToTray,personalization,schemaVersion,theme,uiStyle' || value.schemaVersion !== 1 || !['system', 'light', 'dark', 'sakura', 'ocean', 'forest', 'sand', 'graphite'].includes(value.theme) || !['simple', 'codex', 'cursor', 'web', 'anime', 'glass'].includes(value.uiStyle) || typeof value.closeToTray !== 'boolean') {
      report(defaultsPath, 1, 'settings-schema', 'Expected schema v1 with schemaVersion, theme, uiStyle, closeToTray and personalization. Update the contract intentionally when the schema changes.');
    }
    const p = value.personalization;
    const keys = (object, expected) => object && !Array.isArray(object) && Object.keys(object).sort().join(',') === expected;
    const range = (number, min, max) => Number.isInteger(number) && number >= min && number <= max;
    if (!keys(p, 'pet') ||
      !keys(p.pet, 'alwaysOnTop,animated,enabled,mode,modelId,size') || typeof p.pet.enabled !== 'boolean' || !['desktop','in-app'].includes(p.pet.mode) || p.pet.modelId !== null || !range(p.pet.size,96,256) || typeof p.pet.alwaysOnTop !== 'boolean' || typeof p.pet.animated !== 'boolean') {
      report(defaultsPath, 1, 'settings-schema', 'Personalization defaults must contain only the supported companion settings; retired background and atmosphere fields must not be emitted.');
    }
  } catch (error) { report(defaultsPath, 1, 'settings-schema', error.message); }
  for (const path of filesBelow(resolve(root, 'apps/framework/desktop/src-tauri/capabilities')).filter((path) => path.endsWith('.json'))) {
    try {
      const capabilities = JSON.parse(readFileSync(path, 'utf8'));
      for (const permission of capabilities.permissions ?? []) {
        const identifier = typeof permission === 'string' ? permission : permission.identifier;
        if (typeof identifier === 'string' && /^(?:shell:|process:allow-(?:spawn|exec))/.test(identifier)) report(rel(path), 1, 'execution-capability', `Generic process/shell permission is outside the current contract: ${identifier}`);
      }
    } catch (error) { report(rel(path), 1, 'capabilities-json', error.message); }
  }
}

function selfTest() {
  const file = 'apps/framework/frontend/src/components/Fixture.ts';
  const expect = (source, rule, inspect = inspectScript, fixture = file) => {
    errors.length = 0;
    inspect(fixture, source);
    if (rule) assert(errors.some((error) => error.includes(`[${rule}]`)), `Expected ${rule}: ${source}`);
    else assert.deepEqual(errors, [], source);
  };
  expect(`// import bad from '../../../../features/bad/frontend/module';\nconst note = "window.registry = {}; invoke('exec')";`, undefined);
  expect(`import feature from '../../../../features/demo/frontend/module';`, 'framework-dependency');
  expect(`export { feature } from '../../../../features/demo/frontend/module';`, 'framework-dependency');
  expect(`const load = () => import('../../../../features/demo/frontend/module');`, 'framework-dependency');
  expect(`import { invoke } from '@tauri-apps/api/core';`, 'host-adapter');
  expect(`window['registry'] = {};`, 'global-registry');
  expect(`Object.defineProperty(globalThis, 'registry', { value: {} });`, 'global-registry');
  expect(`window.addEventListener('resize', () => {});`, undefined);
  expect(`request('shell.exec', {});`, 'execution-capability');
  expect(`invoke('execute_command', {});`, 'execution-capability');
  expect(`request();`, undefined);
  expect(`import B from '../../beta/frontend/module';`, 'feature-dependency', inspectScript, 'apps/features/alpha/frontend/module.ts');
  expect(`<template><p>window.registry = {}</p></template><script setup lang="ts">// window.registry = {}\nconst label = 'hello';</script>`, undefined, inspectFrontend, file.replace('.ts', '.vue'));
  expect(`/* #[tauri::command] fn exec() {} /* nested */ */\nconst NOTE: &str = r#"#[tauri::command] fn exec() {}"#;`, undefined, inspectRust, 'apps/framework/desktop/src-tauri/src/fixture.rs');
  expect(`#[tauri::command]\nasync fn exec(command: String) {}`, 'execution-capability', inspectRust, 'apps/framework/desktop/src-tauri/src/fixture.rs');
  expect(`use crate::{settings, features::sample};`, 'framework-dependency', inspectRust, 'apps/framework/desktop/src-tauri/src/fixture.rs');
  errors.length = 0;
  console.log('Boundary scanner self-tests passed: comments/strings ignored; imports, global writes, sibling dependencies and generic commands detected.');
}

try {
  if (process.argv.includes('--self-test')) selfTest();
  checkMetadata();
  const files = filesBelow(resolve(root, 'apps'));
  const python = [];
  let scanned = 0;
  for (const path of files) {
    const file = rel(path);
    const extension = extname(path);
    if (['.ts', '.tsx', '.js', '.jsx', '.mjs', '.vue'].includes(extension)) { inspectFrontend(file, readFileSync(path, 'utf8')); scanned++; }
    else if (extension === '.rs') { inspectRust(file, readFileSync(path, 'utf8')); scanned++; }
    else if (extension === '.py') { python.push(path); scanned++; }
  }
  inspectPython(python);
  if (errors.length) {
    console.error(errors.join('\n'));
    process.exitCode = 1;
  } else console.log(`Architecture boundaries passed (${scanned} source files; documents, settings schema and capabilities checked).`);
  console.log('Coverage: static source imports, direct browser-global writes and named execution bridges. Module ID uniqueness is covered by frontend tests.');
  console.log('Limits: no alias/data-flow analysis, Rust macro expansion, dependency internals, generated code, sandbox guarantee or runtime lifecycle proof. Review new aliases and capability contracts explicitly.');
} catch (error) {
  console.error(`Boundary check could not complete: ${error.message}`);
  process.exitCode = 1;
}
