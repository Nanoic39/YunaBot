<script setup>
import { ref,onMounted } from 'vue';
import { appearanceDefaults } from './appearance.js';
const props=defineProps(['modelValue']);const emit=defineEmits(['update:modelValue','close']);const dialog=ref(null);
function update(key,value){emit('update:modelValue',{...props.modelValue,[key]:value});}
function preset(value){emit('update:modelValue',{...props.modelValue,...value});}
onMounted(()=>dialog.value.showModal());
</script>
<template><dialog ref="dialog" class="appearance-dialog" aria-labelledby="appearance-title" @cancel.prevent="emit('close')"><header class="drawer-head"><div><h3 id="appearance-title">把工作台调成你的习惯</h3><p>立即预览，仅保存在当前浏览器。</p></div><button class="close" aria-label="关闭外观设置" @click="emit('close')">×</button></header><div class="drawer-body">
 <div class="appearance-presets"><button class="appearance-preset" @click="preset({theme:'dark',style:'glass',layout:'sidebar',density:'comfortable'})"><span class="preset-swatch night"></span><strong>夜航玻璃</strong><small>柔和极光 · 分组侧栏</small></button><button class="appearance-preset" @click="preset({theme:'light',style:'plain',layout:'sidebar',density:'comfortable'})"><span class="preset-swatch day"></span><strong>日光工作台</strong><small>清晰浅色 · 简洁面板</small></button><button class="appearance-preset" @click="preset({theme:'dark',style:'plain',layout:'rail',density:'compact'})"><span class="preset-swatch compact"></span><strong>紧凑指挥台</strong><small>图标导航 · 更高信息密度</small></button></div>
 <fieldset v-for="[key,title,options] in [['theme','主题',[['dark','深色'],['light','浅色'],['system','跟随系统']]],['style','视觉风格',[['glass','极光玻璃'],['plain','简洁纯色']]],['layout','桌面布局',[['sidebar','分组侧栏'],['rail','紧凑图标栏'],['top','顶部导航']]],['density','信息密度',[['comfortable','舒适'],['compact','紧凑']]],['motion','动效',[['system','跟随系统'],['reduced','减少动效']]]]" :key="key" class="appearance-field"><legend>{{title}}</legend><div class="appearance-choices"><label v-for="[value,label] in options" :key="value" :class="{selected:modelValue[key]===value}"><input type="radio" :name="'appearance-'+key" :value="value" :checked="modelValue[key]===value" @change="update(key,value)">{{label}}</label></div></fieldset>
 <p class="muted small">手机始终使用抽屉导航。切换外观不会刷新页面或丢弃正在编辑的内容。</p><button class="button ghost" @click="emit('update:modelValue',{...appearanceDefaults})">恢复默认外观</button>
 </div></dialog></template>
