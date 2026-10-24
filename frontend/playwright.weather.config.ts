import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
const port=Number(process.env.WEATHER_E2E_PORT||8021)
export default defineConfig({
 testDir:'./tests',testMatch:'weather-reminder.spec.ts',fullyParallel:false,workers:1,timeout:30000,
 use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:390,height:844},headless:true,launchOptions:browserLaunchOptions},
 reporter:[['list']],outputDir:'../.test-results/weather',
 webServer:{command:testServerCommand('weather_test_server.py'),url:`http://127.0.0.1:${port}/api/health`,reuseExistingServer:false,timeout:20000}
})
