import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
const port=Number(process.env.E2E_REFERENCE_PORT||8050)
export default defineConfig({
 testDir:'./tests',testMatch:'assistant-reference.spec.ts',workers:1,timeout:30000,
 use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:465,height:769},headless:true,timezoneId:'UTC',
 launchOptions:browserLaunchOptions},
 reporter:[['list']],outputDir:'../.test-results/reference-route',
 webServer:{command:testServerCommand('reference_route_test_server.py'),url:`http://127.0.0.1:${port}/api/health`,reuseExistingServer:false,timeout:20000}
})
