import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
export default defineConfig({
 testDir:'./tests',testMatch:'amap-map.spec.ts',workers:1,timeout:35000,
 use:{baseURL:'http://127.0.0.1:8023',viewport:{width:375,height:812},headless:true,
  launchOptions:browserLaunchOptions},
 reporter:[['list'],['json',{outputFile:'../.test-results/amap-m42-browser.json'}]],outputDir:'../.test-results/amap-m42-browser',
 webServer:{command:testServerCommand('amap_map_test_server.py'),url:'http://127.0.0.1:8023/api/health',reuseExistingServer:false,timeout:20000}
})
