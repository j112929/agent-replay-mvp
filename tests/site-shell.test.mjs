import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../dist/site-shell.js',import.meta.url),'utf8').replaceAll('export ','');
function shell(values={},blocked=false){
 const store=new Map(Object.entries(values));
 const context=vm.createContext({localStorage:{getItem:k=>{if(blocked)throw Error('disabled');return store.get(k)||null},setItem:(k,v)=>{if(blocked)throw Error('disabled');store.set(k,v)}},document:{documentElement:{lang:'en'}}});
 vm.runInContext(source+'\nglobalThis.shell={getLanguage,setLanguage,sidebar,topbar};',context);
 return {api:context.shell,store,context};
}
test('language migrates from refund and persists across page instances',()=>{
 const a=shell({'replay-refund-language':'zh'});assert.equal(a.api.getLanguage(),'zh');
 a.api.setLanguage('en');assert.equal(a.store.get('replay-language'),'en');
 const b=shell(Object.fromEntries(a.store));assert.equal(b.api.getLanguage(),'en');
 assert.match(b.api.sidebar('refund'),/evaluation-en.html/);
});
test('all pages have four destinations and exactly one active page',()=>{
 const {api}=shell();for(const page of ['home','replay','benchmarks','refund']){
 const html=api.sidebar(page);assert.equal((html.match(/aria-current="page"/g)||[]).length,1);
 assert.equal((html.match(/class="nav-icon"/g)||[]).length,4);
 assert.match(api.topbar(page),/aria-pressed="true"/);
 }
});
test('disabled storage and invalid preferences cannot break navigation',()=>{
 const a=shell({},true);a.api.setLanguage('zh');assert.equal(a.api.getLanguage(),'zh');assert.match(a.api.sidebar('replay'),/回放与回归/);
 a.api.setLanguage('invalid');assert.equal(a.api.getLanguage(),'zh');
 assert.equal(shell({'replay-language':'invalid'}).api.getLanguage(),'en');
});
