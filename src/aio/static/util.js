// Pure, DOM-free helpers for the dashboard, split out of app.js so they can be
// unit-tested with `node --test` (see tests/frontend/util.test.mjs). No browser
// globals are touched here — everything is string in, string/object out.

export function short(v) {
  v = String(v).replace(/\n/g, '\\n');
  return v.length > 60 ? v.slice(0, 60) + '…' : v;
}

export function esc(s) {
  return String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

export function parseEnv(s) {
  const env = {};
  (s || '').split(/[\s\n]+/).forEach(tok => {
    const i = tok.indexOf('=');
    if (i > 0) { env[tok.slice(0, i)] = tok.slice(i + 1); }
  });
  return env;
}

// --- tiny zero-dependency syntax highlighter ---
const KW = {
  python: 'def class return if elif else for while import from as with try except finally raise in not and or is None True False lambda yield global nonlocal pass break continue assert del async await self print',
  js: 'function return if else for while var let const new class extends import export default from typeof instanceof in of await async yield try catch finally throw switch case break continue this null true false undefined delete void do',
  shell: 'if then fi else elif for do done case esac in function while until select export local return',
};
function kw(l) { return '\\b(?:' + KW[l].trim().split(/\s+/).join('|') + ')\\b'; }
const STR = "'(?:\\\\.|[^'\\\\])*'|\"(?:\\\\.|[^\"\\\\])*\"";
const LANGS = {
  python: { comment: '#.*', string: "'''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\"|" + STR, keyword: kw('python'), number: '\\b\\d[\\d_]*\\.?\\d*\\b' },
  js: { comment: '//.*|/\\*[\\s\\S]*?\\*/', string: STR + "|`(?:\\\\.|[^`\\\\])*`", keyword: kw('js'), number: '\\b\\d[\\d_]*\\.?\\d*\\b' },
  json: { string: '"(?:\\\\.|[^"\\\\])*"', keyword: '\\b(?:true|false|null)\\b', number: '-?\\b\\d[\\d_]*\\.?\\d*(?:[eE][+-]?\\d+)?\\b' },
  css: { comment: '/\\*[\\s\\S]*?\\*/', atrule: '@[\\w-]+', string: STR, number: '-?\\b\\d*\\.?\\d+(?:px|em|rem|%|vh|vw|s|ms|fr|deg)?\\b' },
  html: { comment: '<!--[\\s\\S]*?-->', tag: '</?[a-zA-Z][\\w:-]*|/?>', string: STR },
  shell: { comment: '#.*', string: STR, keyword: kw('shell'), number: '\\b\\d+\\b' },
  md: { heading: '^#{1,6}.*', code: '```[\\s\\S]*?```|`[^`]*`' },
};
const EXT = {
  py: 'python', pyw: 'python', js: 'js', jsx: 'js', ts: 'js', tsx: 'js', mjs: 'js', json: 'json',
  css: 'css', scss: 'css', html: 'html', htm: 'html', xml: 'html', sh: 'shell', bash: 'shell', md: 'md', markdown: 'md',
};
const RX = {};
function compile(lang) {
  if (RX[lang]) return RX[lang];
  const spec = LANGS[lang]; const keys = Object.keys(spec);
  RX[lang] = { keys, re: new RegExp(keys.map(k => '(?<' + k + '>' + spec[k] + ')').join('|'), 'gms') };
  return RX[lang];
}

export function langFor(path) {
  const m = (path || '').match(/\.([A-Za-z0-9]+)$/);
  return m ? (EXT[m[1].toLowerCase()] || null) : null;
}

export function highlight(code, lang) {
  if (!lang || !LANGS[lang]) return esc(code);
  const { re } = compile(lang); re.lastIndex = 0; let out = '', last = 0, m;
  while ((m = re.exec(code))) {
    out += esc(code.slice(last, m.index));
    const g = Object.keys(m.groups).find(k => m.groups[k] !== undefined);
    out += '<span class="t-' + g + '">' + esc(m[0]) + '</span>';
    last = m.index + m[0].length;
    if (m[0].length === 0) re.lastIndex++;
  }
  out += esc(code.slice(last)); return out;
}
