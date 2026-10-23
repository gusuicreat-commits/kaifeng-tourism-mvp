import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
const port=Number(process.env.E2E_PORT||8018)
export default defineConfig({
 testDir:'./tests',testIgnore:['agent-v3.spec.ts','assistant-reference.spec.ts'],fullyParallel:false,workers:1,timeout:30000,
 use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:1440,height:1000},headless:true,
  launchOptions:{args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'],...browserLaunchOptions}},
 reporter:[['list']],outputDir:'../.test-results',
 webServer:{command:testServerCommand('browser_server.py'),url:`http://127.0.0.1:${port}/api/health`,reuseExistingServer:false,timeout:20000}
})
