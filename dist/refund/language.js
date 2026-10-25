import {getLanguage, mountNavigation} from '/site-shell.js';
const paths = {zh: '/refund/evaluation.html', en: '/refund/evaluation-en.html'};
const current = document.documentElement.lang === 'en' ? 'en' : 'zh';
if (getLanguage() !== current) location.replace(paths[getLanguage()]);
else mountNavigation('refund', () => location.assign(paths[getLanguage()]));
