/**
 * 前端初始化顺序守卫。
 *
 * 分段审核面板把 DOM 先插进 body、再 await 拉数据，回调里的 let/const 一旦
 * 在声明之前被读到就会抛 "Cannot access 'x' before initialization"（TDZ）。
 * 这类错误在浏览器外看不出来，所以在这里静态检查两件事：
 *   1. 事件绑定与子节点挂载不得早于该元素的声明；
 *   2. 面板函数里不得在声明前读 let/const 变量。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../web/h3lv.js', import.meta.url), 'utf8');
// Windows 上 core.autocrlf=true 会把检出文件转成 CRLF，按行切割必须两种都认。
const lines = source.split(/\r?\n/);

// 模块级函数与浏览器全局：出现即视为已就绪。
const GLOBALS = new Set([
  'element', 'actionButton', 'request', 'api', 'app', 'toast', 'document', 'window',
  'confirmDialog', 'messageDialog', 'owner', 'console', 'JSON', 'Math', 'Number',
  'String', 'Boolean', 'Array', 'Object', 'Promise', 'Date', 'URLSearchParams',
  'setTimeout', 'clearTimeout', 'fetch', 'SHOT_SIZES', 'createTimeline', 'ctx',
]);

function panelBody() {
  const start = lines.findIndex(line => line.includes('async function openReview'));
  assert.ok(start >= 0, '找不到 openReview');
  const end = lines.findIndex((line, index) => index > start && line === '}');
  assert.ok(end > start, '找不到 openReview 结尾');
  return {start, end};
}

function orderProblems() {
  const {start, end} = panelBody();
  const declared = new Set(GLOBALS);
  const problems = [];
  for (let index = start + 1; index < end; index += 1) {
    const line = lines[index];
    if (/^\s*\*/.test(line) || /^\s*\/\//.test(line)) continue;
    for (const match of line.matchAll(/(?:const|let)\s+([A-Za-z_$][\w$]*)/g)) declared.add(match[1]);
    // 只检查“一写到已声明元素上”的语句，函数体内部另算（延迟执行）。
    const bind = line.match(/^\s{2}([A-Za-z_$][\w$]*)\s*\.\s*(?:on\w+|addEventListener|append|appendChild|setAttribute|replaceChildren)\s*[=(]/);
    if (bind && !declared.has(bind[1])) problems.push(`${index + 1}: ${line.trim()}`);
  }
  return problems;
}

test('面板元素在绑定事件或挂载子节点之前已经声明', () => {
  assert.deepEqual(orderProblems(), []);
});

test('面板状态在首次 await 之前初始化', () => {
  const {start, end} = panelBody();
  const firstAwait = lines.findIndex((line, index) => index > start && index < end
    && !/^\s*\/\//.test(line) && /^\s{2}.*\bawait\b/.test(line));
  const firstDirty = lines.findIndex((line, index) => index > start && index < end && /^\s{2}let dirty\b/.test(line));
  assert.ok(firstDirty > start, '面板必须在 openReview 内声明 dirty');
  if (firstAwait >= 0 && firstDirty >= 0) {
    assert.ok(firstDirty < firstAwait, `dirty 应在第一个 await（第 ${firstAwait + 1} 行）之前声明`);
  }
});
