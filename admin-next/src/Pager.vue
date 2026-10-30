<script setup>
import { computed } from 'vue';
const props = defineProps({ total: { type: Number, default: 0 }, page: { type: Number, default: 0 }, size: { type: Number, default: 15 } });
const emit = defineEmits(['update:page']);
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.size)));
const current = computed(() => Math.min(props.page, pages.value - 1));
function go(value) { emit('update:page', Math.max(0, Math.min(pages.value - 1, value))); }
</script>
<template>
  <div v-if="total > size" class="pager">
    <button type="button" :disabled="current <= 0" @click="go(current - 1)">上一页</button>
    <span>{{ current + 1 }} / {{ pages }} · 共 {{ total }} 条</span>
    <button type="button" :disabled="current >= pages - 1" @click="go(current + 1)">下一页</button>
  </div>
</template>
