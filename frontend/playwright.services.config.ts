import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
export default defineConfig({
 testDir:'./tests',testMatch:'travel-services.spec.ts',workers:1,timeout:30000,
 use:{baseURL:'http://127.0.0.1:8027',viewport:{width:390,height:844},headless:true,launchOptions:browserLaunchOptions},
 reporter:[['list']],outputDir:'../.test-results/services',
 webServer:{command:testServerCommand('travel_services_test_server.py'),url:'http://127.0.0.1:8027/api/health',reuseExistingServer:false,timeout:20000}
})
