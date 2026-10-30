/* 在首屏绘制前应用主题，避免深色模式闪白。CSP 允许同源脚本。 */
(function () {
  try {
    var saved = localStorage.getItem('yuna-theme');
    var dark = saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    var theme = dark ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', dark ? '#12111b' : '#f4f3f8');
  } catch (error) {
    document.documentElement.dataset.theme = 'light';
  }
})();
