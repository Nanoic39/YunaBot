import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig({
  plugins: [vue()],
  base: '/next/',
  build: { outDir: '../src/admin/static/next', emptyOutDir: true, sourcemap: false },
});
