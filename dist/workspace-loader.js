// A static Vercel site and the local SDK retain the existing debugger.
let service;
try {
  const response = await fetch('/api/saas/config',{credentials:'same-origin',cache:'no-store'});
  if (response.ok) service = await response.json();
} catch { /* The legacy debugger supports an offline/static preview. */ }
if (service?.service === 'agent-regression-guard') {
  const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/guard-ui.css'; document.head.append(link);
  const {start} = await import('./guard-ui.js'); await start(service);
} else await import('./dashboard.js');
