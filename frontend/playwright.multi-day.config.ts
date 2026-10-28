import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
const port=Number(process.env.E2E_MULTI_DAY_PORT||8055)
export default defineConfig({
 testDir:'./tests',testMatch:'multi-day.spec.ts',workers:1,timeout:30000,
 use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:390,height:844},headless:true,timezoneId:'UTC',launchOptions:browserLaunchOptions},
 reporter:'list',outputDir:'../.test-results/multi-day',
 webServer:{command:testServerCommand('multi_day_test_server.py'),url:`http://127.0.0.1:${port}/api/health`,reuseExistingServer:false,timeout:20000}
})
