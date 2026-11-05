import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('./client.js',import.meta.url),'utf8');
let definition;
const sandbox={window:{__ModuleLoader__:{load:x=>{definition=x;}}},AbortController,Uint8Array,btoa,console};
vm.runInNewContext(source,sandbox);
const registrations=[];
const calls=[];
let failSelection=false;
const ctx={
 locale:{register:()=>()=>{},bind:()=>x=>x},effect:f=>f(),
 slots:{inject:(_key,f)=>f(),register:(options)=>{registrations.push(options);return ()=>{};}},
 workspaces:{create:async()=>({workspaceId:'w'})},
 uiWorkspace:{connectWorkspace:async()=>{calls.push('connect');return 's';},startSession:(id,opts)=>calls.push(['draft',id,opts]),openSession:id=>calls.push(['open',id])},
 remote:{workspaceFiles:{},fileUploads:{upload:async(id,payload)=>{calls.push(['upload',id,payload.name]);return {ok:true,value:{receiptId:'r'}};}},session:{
  modelCatalog:async()=>({ok:true,value:{default:{provider:'p',model:'m'},groups:[],failures:[]}}),
  selectModel:async request=>{calls.push(['select',request]);return failSelection?{ok:false,error:{message:'denied'}}:{ok:true,value:{selected:request}};}
 }},
 sessions:{using:async(id,opts,fn)=>fn({ready:Promise.resolve({session:{prompt:async(parts)=>{calls.push(['prompt',id,parts]);return {ok:true,value:{accepted:true}};}}})})}
};
definition.factory(()=>({createElement(){}})).apply(ctx);
const api=registrations.find(x=>x.name==='main').inject();
assert.equal((await api.modelCatalog()).default.model,'m');
await api.start('w',{prompt:'draft'}, {provider:'p',model:'m',reasoningEffort:'medium'});
assert.equal(calls[0],'connect');
assert.equal(calls[1][0],'select');
assert.equal(calls[1][1].sessionId,'s');
assert.equal(calls[1][1].reasoningEffort,'medium');
assert.equal(calls[2][0],'draft');
calls.length=0;
await api.start('w',{prompt:'draft'}, null);
assert.equal(calls[0],'connect');
assert.equal(calls[1][0],'draft');
calls.length=0;
await api.send('w','text',[{name:'source.txt',arrayBuffer:async()=>new TextEncoder().encode('abc').buffer}],{provider:'p',model:'m',reasoningEffort:'high'});
assert.equal(calls[1][0],'select');
assert.equal(calls[1][1].reasoningEffort,'high');
assert.equal(calls[2][0],'upload');
assert.equal(calls[3][0],'prompt');
assert.equal(calls[3][1],'s');
assert.equal(calls[3][2][1].receiptId,'r');
calls.length=0; failSelection=true;
await assert.rejects(api.send('w','text',[],{provider:'p',model:'m'}),/denied/);
assert.equal(calls.length,2);
calls.length=0;
await assert.rejects(api.start('w',{prompt:'draft'},{provider:'p',model:'m'}),/denied/);
assert.equal(calls.length,2);
const picker=source.match(/onChange:e=>\{const added=Array\.from\(e\.target\.files \|\| \[\]\);e\.target\.value='';if\(added\.length\)setFiles\(current=>\[\.\.\.current,\.\.\.added\]\);setError\(''\);\}/);
assert.ok(picker,'file picker must append and reset');
let attachments=[];
const fileContext={setFiles:update=>{attachments=update(attachments);},setError:()=>{}};
const onChange=vm.runInNewContext('('+picker[0].slice('onChange:'.length)+')',fileContext);
const a={name:'a.docx'},b={name:'b.docx'};
const first={files:[a],value:'a.docx'};
onChange({target:first});
assert.equal(first.value,'');
onChange({target:{files:[b],value:'b.docx'}});
assert.equal(attachments.length,2);
assert.equal(attachments[0],a);
assert.equal(attachments[1],b);
const remove=source.match(/onClick:\(\)=>setFiles\(current=>current\.filter\(\(_,n\)=>n!==i\)\)/);
assert.ok(remove,'file removal callback must target one index');
fileContext.i=0;
vm.runInNewContext('('+remove[0].slice('onClick:'.length)+')()',fileContext);
assert.equal(attachments[0],b);
onChange({target:{files:[a],value:'a.docx'}});
assert.equal(attachments.length,2);
assert.equal(attachments[1],a);
onChange({target:{files:[],value:''}});
assert.equal(attachments.length,2);
console.log('PASS: multiple file selections append, selected files can be removed and re-added, and cancellation preserves the list.');
console.log('PASS: catalog, model selection before draft/upload, same-session attachment send, and selection failure stops both paths.');
