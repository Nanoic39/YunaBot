import {test} from 'node:test';
import assert from 'node:assert/strict';
import {appearanceDefaults,normalizeAppearance,applyAppearance,loadAppearance} from './appearance.js';
test('unknown preferences cannot create unhandled themes or layouts',()=>{
 assert.deepEqual(normalizeAppearance({theme:'evil',layout:'../../',style:null}),appearanceDefaults);
 assert.deepEqual(loadAppearance({getItem(){throw new Error('blocked storage');}}),appearanceDefaults);
 assert.deepEqual(loadAppearance({getItem(){return 'broken json';}}),appearanceDefaults);
});
test('system theme resolves independently of style, layout and density',()=>{
 const root={dataset:{},style:{}};
 applyAppearance({theme:'system',style:'plain',layout:'rail',density:'compact',motion:'reduced'},root,false);
 assert.deepEqual(root.dataset,{theme:'light',style:'plain',layout:'rail',density:'compact',motion:'reduced'});
 assert.equal(root.style.colorScheme,'light');
 applyAppearance({theme:'system'},root,true);assert.equal(root.dataset.theme,'dark');
});
