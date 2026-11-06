import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {join} from 'node:path';
import {apply} from './index.js';
const registrations=[];
const disposers=[];
apply({skills:{register(skill){registrations.push(skill);return ()=>{};}},effect(fn){disposers.push(fn());}});
assert.equal(registrations.length,2);
for(const skill of registrations){
  assert.equal(skill.source,'runtime');
  assert.equal(typeof skill.content,'string');
  assert.ok(skill.content.length>100);
  assert.equal(skill.invocation.modelInvocable,true);
}
const format=registrations.find(x=>x.name==='official-document-formatting');
assert.equal(format.resourceBase.kind,'directory');
assert.ok(existsSync(format.path));
assert.ok(existsSync(join(format.resourceBase.path,'format_document.py')));
assert.ok(existsSync(join(format.resourceBase.path,'import_document.py')));
assert.ok(existsSync(join(format.resourceBase.path,'preserve_docx.py')));
for(const text of ['方正小标宋_GBK','方正黑体_GBK','方正楷体_GBK','方正仿宋_GBK','Times New Roman','28磅','25mm','标题前空一行','标题后空一行','三级标题','与其后正文同段接排','--check','混排','WPS','默认由主智能体独立完成','用户明确要求或同意后','最多并行2个子智能体','推理档位默认 `low`','不要把子智能体的完整推理过程']) assert.ok(format.content.includes(text),text);
assert.equal(disposers.length,2);
console.log('PASS: both runtime skills have valid source/content, and formatting resources resolve to the packaged Python script.');
