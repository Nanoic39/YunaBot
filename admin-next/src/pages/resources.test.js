import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reconcile, errorMessage } from './resources.js';
test('data refresh retains record references and the order being edited',()=>{
 const a={id:'a',enabled:true}, b={id:'b',enabled:false}, rows=[a,b];
 reconcile(rows,[{id:'b',enabled:true},{id:'a',enabled:false},{id:'c',enabled:true}]);
 assert.deepEqual(rows.map(r=>r.id),['a','b','c']);assert.equal(rows[0],a);assert.equal(rows[1],b);assert.equal(a.enabled,false);
 reconcile(rows,[{id:'b',enabled:false}]);assert.equal(rows.length,1);assert.equal(rows[0],b);
});
test('HTML proxy errors produce a short readable message',()=>{
 assert.equal(errorMessage(new Error('<html>proxy failure</html>')),'服务暂时不可用，请稍后重试');
 assert.equal(errorMessage(new Error('未修改额度')),'未修改额度');
});
