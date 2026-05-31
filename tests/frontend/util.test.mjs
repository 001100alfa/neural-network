// Behavioural unit tests for the dashboard's pure logic module (util.js),
// run with `node --test` (no browser needed). Driven from tests/test_frontend.py
// so they execute in CI alongside the Python suite.

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { short, esc, parseEnv, langFor, highlight } from '../../src/aio/static/util.js';

test('short truncates long values and escapes newlines', () => {
  assert.equal(short('hi'), 'hi');
  assert.equal(short('a\nb'), 'a\\nb');
  const long = 'x'.repeat(80);
  const out = short(long);
  assert.ok(out.endsWith('…'));
  assert.equal(out.length, 61); // 60 chars + ellipsis
});

test('esc escapes HTML metacharacters', () => {
  assert.equal(esc('<a> & </a>'), '&lt;a&gt; &amp; &lt;/a&gt;');
  assert.equal(esc('plain'), 'plain');
});

test('parseEnv parses KEY=VALUE tokens, ignoring junk', () => {
  assert.deepEqual(parseEnv('A=1 B=2'), { A: '1', B: '2' });
  assert.deepEqual(parseEnv('TOKEN=abc=def'), { TOKEN: 'abc=def' }); // only first =
  assert.deepEqual(parseEnv(''), {});
  assert.deepEqual(parseEnv('noequals'), {});
  assert.deepEqual(parseEnv('  X=9\n Y=10 '), { X: '9', Y: '10' });
});

test('langFor maps file extensions to highlighter languages', () => {
  assert.equal(langFor('main.py'), 'python');
  assert.equal(langFor('a/b/app.tsx'), 'js');
  assert.equal(langFor('style.scss'), 'css');
  assert.equal(langFor('README.md'), 'md');
  assert.equal(langFor('noext'), null);
  assert.equal(langFor('data.unknownext'), null);
});

test('highlight wraps tokens in typed spans and escapes the rest', () => {
  const out = highlight('def f(): return 1', 'python');
  assert.ok(out.includes('<span class="t-keyword">def</span>'));
  assert.ok(out.includes('<span class="t-keyword">return</span>'));
  assert.ok(out.includes('<span class="t-number">1</span>'));
});

test('highlight escapes HTML so code cannot inject markup', () => {
  const out = highlight('x = "<script>"', 'python');
  assert.ok(!out.includes('<script>'));
  assert.ok(out.includes('&lt;script&gt;'));
});

test('highlight with unknown language just escapes', () => {
  assert.equal(highlight('a < b', null), 'a &lt; b');
  assert.equal(highlight('a < b', 'klingon'), 'a &lt; b');
});
