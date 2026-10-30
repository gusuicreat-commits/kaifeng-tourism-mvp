import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
import {fileURLToPath} from 'node:url'
import {writeFacts} from './playwright.full-flow.fixture.mjs'

process.env.E2E_CITY_FACTS=writeFacts()
process.env.E2E_OBJECT_BINDINGS=fileURLToPath(new URL('../.test-results/full-flow/object_bindings.json',import.meta.url))
process.env.E2E_JOURNEYS=fileURLToPath(new URL('../.test-results/full-flow/journeys.json',import.meta.url))
const port=Number(process.env.E2E_FULL_FLOW_PORT||8024)
export default defineConfig({
 testDir:'./tests',testMatch:'full-flow.spec.ts',fullyParallel:false,workers:1,timeout:30000,
 use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:1440,height:1000},headless:true,launchOptions:browserLaunchOptions},
 reporter:[['list']],outputDir:'../.test-results/full-flow',
 // Playwright clears outputDir after loading this config, before starting the server.
 webServer:{command:`node playwright.full-flow.fixture.mjs && ${testServerCommand('browser_server.py')}`,env:{E2E_PORT:String(port)},url:`http://127.0.0.1:${port}/api/health`,reuseExistingServer:false,timeout:20000}
})
