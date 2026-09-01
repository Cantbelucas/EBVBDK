/* Koeres synkront i <head>. Saetter temaet for CSS'en naas, ellers
   blinker siden i den forkerte tone inden valget rammer. */
try {
  var saved = localStorage.getItem('ebvb-theme');
  document.documentElement.dataset.theme = saved
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
} catch (e) {}
