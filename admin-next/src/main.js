import { createApp } from 'vue';
import App from './App.vue';
import './style.css';
import {loadAppearance,applyAppearance} from './appearance.js';
applyAppearance(loadAppearance(localStorage),document.documentElement,matchMedia('(prefers-color-scheme: dark)').matches);
createApp(App).mount('#app');
