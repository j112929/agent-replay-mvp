// Shared navigation and language preference. Never reads or modifies trajectory storage.
const storageKey = 'replay-language';
let language;
try { language = localStorage.getItem(storageKey) || localStorage.getItem('replay-refund-language'); } catch {}
if (!['zh', 'en'].includes(language)) language = document.documentElement.lang === 'zh-CN' ? 'zh' : 'en';
export const getLanguage = () => language;
export const text = (en, zh) => language === 'zh' ? zh : en;
export function setLanguage(value) {
  if (!['zh', 'en'].includes(value)) return;
  language = value;
  try { localStorage.setItem(storageKey, value); localStorage.setItem('replay-refund-language', value); } catch {}
  document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en';
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('replay-language-change'));
}
export function sidebar(page) {
  const items = [['home', '/', '◈', text('Overview', '总览')], ['replay', '/debugger.html', '⇄', text('Replay & regression', '回放与回归')], ['benchmarks', '/benchmark.html', '◷', text('Benchmarks', '基准测试')], ['refund', language === 'en' ? '/refund/evaluation-en.html' : '/refund/evaluation.html', '✓', text('Refund evaluation', 'Refund 评估')]];
  return `<aside class="sidebar"><a class="brand" href="/"><span class="brand-mark" aria-hidden="true">↳</span> replay <span class="version">0.4</span></a><div class="nav-group-label">${text('Workspace','工作台')}</div><nav class="nav site-navigation" aria-label="${text('Main navigation','主站导航')}">${items.map(([key,href,icon,label])=>`${key==='refund'?`<div class="nav-group-label">${text('Evaluation reports','评估报告')}</div>`:''}<a href="${href}" ${key===page?'class="active" aria-current="page"':''}><span class="nav-icon" aria-hidden="true">${icon}</span><span>${label}</span></a>`).join('')}</nav><div class="sidebar-footer"><p>${text('Aggregate evidence viewer.','执行证据汇总。')}<br>${text('Source and dataset ZIP files remain local.','源码和数据集 ZIP 保留本地。')}</p><a href="https://github.com/j112929/agent-replay-mvp" target="_blank" rel="noreferrer">${text('Source on GitHub ↗','GitHub 源码 ↗')}</a></div></aside>`;
}
export function topbar(page) {
  const labels = {home:text('Overview','总览'),replay:text('Replay & regression','回放与回归'),benchmarks:text('Benchmarks','基准测试'),refund:text('Refund evaluation','Refund 评估')};
  return `<div class="topbar"><span>${text('Workspace','工作台')} &nbsp; / &nbsp; <strong>${labels[page]}</strong></span><nav class="language-switch" aria-label="${text('Language','语言')}"><button type="button" data-language="zh" lang="zh-CN" aria-pressed="${language==='zh'}">中文</button><button type="button" data-language="en" lang="en" aria-pressed="${language==='en'}">English</button></nav></div>`;
}
export function bindNavigation(onChange) {
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-language]').forEach(button=>button.addEventListener('click',event=>{
    event.preventDefault(); setLanguage(button.dataset.language); onChange();
  }));
}
export function mountNavigation(page, onChange) {
  document.querySelector('.sidebar')?.replaceWith(fragment(sidebar(page)));
  document.querySelector('.topbar')?.replaceWith(fragment(topbar(page)));
  bindNavigation(onChange);
}
function fragment(html) { const template = document.createElement('template'); template.innerHTML = html; return template.content; }
