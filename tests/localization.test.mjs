import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const catalog=fs.readFileSync(new URL('../dist/copy.js',import.meta.url),'utf8').replaceAll('export ','');
const localization=fs.readFileSync(new URL('../dist/localization.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replaceAll('export ','');
function environment(){
 let lang='zh';const handlers={};const nodes=[{nodeValue:'Metric',parentElement:{closest:()=>null}},{nodeValue:'Metric',parentElement:{closest:()=>true}},{nodeValue:'Failures (7)',parentElement:{closest:()=>null}}];
 const doc={title:'Replay',body:{},createTreeWalker(){let i=0;return {nextNode:()=>nodes[i++]||null}},querySelectorAll:()=>[]};
 const context=vm.createContext({document:doc,NodeFilter:{SHOW_TEXT:4},getLanguage:()=>lang,mountNavigation:()=>{},window:{addEventListener:(key,fn)=>handlers[key]=fn},MutationObserver:class{observe(){}}});
 vm.runInContext(catalog+'\n'+localization+'\nglobalThis.api={translate,installLocalization,parseUIJSON,showError};',context);
 return {api:context.api,nodes,change:value=>{lang=value;handlers['replay-language-change']()},doc};
}
test('language switching is reversible, protects evidence, and preserves node identity',()=>{
 const e=environment(),original=e.nodes[0];e.api.installLocalization('home');
 assert.equal(e.nodes[0].nodeValue,'指标');assert.equal(e.nodes[1].nodeValue,'Metric');assert.equal(e.nodes[2].nodeValue,'失败记录 (7)');
 e.change('en');assert.equal(e.nodes[0],original);assert.equal(e.nodes[0].nodeValue,'Metric');assert.equal(e.nodes[2].nodeValue,'Failures (7)');
 e.change('zh');assert.equal(e.nodes[0].nodeValue,'指标');
});
test('copy translation preserves denominators, whitespace, and unknown diagnostic data',()=>{
 const {api}=environment();assert.equal(api.translate(' 6 / 9 passed ','zh'),' 6 / 9 通过 ');
 assert.equal(api.translate('provider raw diagnostic xyz','zh'),'provider raw diagnostic xyz');
 assert.equal(api.translate('Not measured','zh'),'未测量');assert.equal(api.translate('Not measured','en'),'Not measured');
});

test('malformed imported or draft JSON has a stable bilingual validation message',()=>{
 const {api}=environment();assert.throws(()=>api.parseUIJSON('{broken'),/Invalid JSON. Check the file or draft syntax./);
 assert.equal(api.translate('Invalid JSON. Check the file or draft syntax.','zh'),'JSON 格式无效，请检查文件或草稿语法。');
 assert.equal(api.translate('Invalid regression result','zh'),'回归结果格式无效');
 assert.equal(api.translate('id must be a non-empty string (max 240 characters).','zh'),'id 必须为非空字符串（最多 240 个字符）。');
 assert.equal(api.parseUIJSON('{"value":7}').value,7);
});
test('unknown service diagnostics get a translated explanation and literal evidence',()=>{
 const e=environment();e.doc.createElement=()=>({dataset:{},textContent:''});
 const element={replaceChildren(...children){this.children=children;}};
 e.api.showError(element,new Error('provider diagnostic xyz'));
 assert.equal(element.children[0].textContent,'Operation failed. Details: ');
 assert.equal(e.api.translate(element.children[0].textContent,'zh'),'操作失败。详情： ');
 assert.equal(element.children[1].textContent,'provider diagnostic xyz');
 assert.equal(element.children[1].dataset.evidence,'');
});
