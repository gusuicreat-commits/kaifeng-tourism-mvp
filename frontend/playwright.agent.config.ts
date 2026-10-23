import {defineConfig} from '@playwright/test'
import {browserLaunchOptions,testServerCommand} from './playwright.runtime'
export default defineConfig({
 testDir:'./tests',testMatch:'agent-v3.spec.ts',workers:1,timeout:30000,
 use:{baseURL:'http://127.0.0.1:8021',viewport:{width:375,height:812},headless:true,
  launchOptions:browserLaunchOptions},
 reporter:'list',outputDir:'../.test-results/agent-v3-browser',
 webServer:{command:testServerCommand('agent_test_server.py'),url:'http://127.0.0.1:8021/api/health',reuseExistingServer:false,timeout:20000}
})
