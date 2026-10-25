import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
import {fileURLToPath} from 'node:url'
// These fixtures are loaded only by the disposable browser test server.
if(process.env.E2E_EMPTY_CATALOG!=='1'){
 process.env.E2E_CITY_FACTS??=fileURLToPath(new URL('../backend/tests/fixtures/city_facts.json',import.meta.url))
 process.env.E2E_OBJECT_BINDINGS??=fileURLToPath(new URL('../backend/tests/fixtures/object_bindings.json',import.meta.url))
}
const port=Number(process.env.E2E_PORT||8018)
export default defineConfig({
 testDir:'./tests',testIgnore:['agent-v3.spec.ts','assistant-reference.spec.ts','travel-services.spec.ts','weather-reminder.spec.ts'],fullyParallel:false,workers:1,timeout:30000,
 use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:1440,height:1000},headless:true,
  launchOptions:{args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'],...browserLaunchOptions}},
 reporter:[['list']],outputDir:'../.test-results',
 webServer:{command:testServerCommand('browser_server.py'),url:`http://127.0.0.1:${port}/api/health`,reuseExistingServer:false,timeout:20000}
})
