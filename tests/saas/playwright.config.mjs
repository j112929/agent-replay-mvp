import {defineConfig} from '@playwright/test';
import {randomUUID} from 'node:crypto';
export default defineConfig({
  testDir:'.',testMatch:'ui.spec.mjs',fullyParallel:false,workers:1,timeout:30000,
  outputDir:'../../.replay/ui-results',reporter:'list',
  use:{baseURL:'http://127.0.0.1:32901',viewport:{width:1440,height:1000},trace:'retain-on-failure',
    launchOptions:process.env.GUARD_BROWSER_EXECUTABLE ? {executablePath:process.env.GUARD_BROWSER_EXECUTABLE,args:['--no-sandbox']} : {}},
  webServer:{command:'node saas/server.mjs',cwd:'../..',url:'http://127.0.0.1:32901/api/health',reuseExistingServer:false,
    env:{GUARD_ORIGIN:'http://127.0.0.1:32901',PORT:'32901',GUARD_DATA_DIR:'.replay/ui-'+randomUUID(),NODE_ENV:'development',GUARD_SMTP_URL:'',STRIPE_SECRET_KEY:'',STRIPE_WEBHOOK_SECRET:''}}
});
