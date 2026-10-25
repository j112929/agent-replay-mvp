import {copy, patterns} from './copy.js';
import {getLanguage, mountNavigation} from './site-shell.js';
export function parseUIJSON(value) {
  try { return JSON.parse(value); }
  catch (error) {
    if (error instanceof SyntaxError) throw new Error('Invalid JSON. Check the file or draft syntax.');
    throw error;
  }
}
export function showError(element, error) {
  const message=String(error?.message || error);
  if (translate(message,'zh') !== message) element.textContent=message;
  else {
    const label=document.createElement('span');label.textContent='Operation failed. Details: ';
    const details=document.createElement('span');details.dataset.evidence='';details.textContent=message;
    element.replaceChildren(label,details);
  }
}
export function translate(value, language) {
  if (language !== 'zh') return value;
  const trimmed=value.trim(), left=value.slice(0,value.indexOf(trimmed));
  const right=value.slice(value.indexOf(trimmed)+trimmed.length);
  if (Object.hasOwn(copy,trimmed)) return left+copy[trimmed]+right;
  for (const [pattern,replacement] of patterns) if(pattern.test(trimmed)) return left+trimmed.replace(pattern,replacement)+right;
  return value;
}
const protectedSelector='pre,code,textarea,option,.diff-values,[data-evidence],.sidebar,.topbar,.language-switch,script,style';
export function installLocalization(page) {
  const originals=new WeakMap();
  const attributes=new WeakMap();
  const originalTitle=document.title;
  const titles={home:['Replay — Rollout Infrastructure','Replay — Rollout 基础设施'],replay:['Replay — Agent Regression Debugger','Replay — Agent 回归调试器'],benchmarks:['Replay — Benchmark evidence','Replay — 基准测试证据']};
  function apply() {
    const language=getLanguage();
    document.title=(titles[page]||[originalTitle,originalTitle])[language==='zh'?1:0];
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let node;
    while((node=walker.nextNode())) {
      if(node.parentElement?.closest(protectedSelector)||!node.nodeValue.trim()) continue;
      const saved=originals.get(node);
      const original=saved&&node.nodeValue===saved.last?saved.original:node.nodeValue;
      const localized=translate(original,language);
      originals.set(node,{original,last:localized});
      if(node.nodeValue!==localized) node.nodeValue=localized;
    }
    for(const element of document.querySelectorAll('[placeholder],[aria-label]')) {
      if(element.closest(protectedSelector))continue;
      let saved=attributes.get(element);if(!saved){saved={};attributes.set(element,saved);}
      for(const name of ['placeholder','aria-label']) {
        const value=element.getAttribute(name);if(value===null)continue;
        const state=saved[name],original=state&&value===state.last?state.original:value;
        const localized=translate(original,language);saved[name]={original,last:localized};
        if(value!==localized)element.setAttribute(name,localized);
      }
    }
  }
  // Switch copy in place: open dialogs, drafts, selected captures and loaded data survive.
  window.addEventListener('replay-language-change',()=>{mountNavigation(page,()=>{});apply();});
  const observer=new MutationObserver(apply);
  observer.observe(document.body,{childList:true,subtree:true,characterData:true});
  apply();
  return apply;
}
