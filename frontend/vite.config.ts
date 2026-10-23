import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig(({ mode }) => {
 const env = loadEnv(mode, '..', '')
 return { envDir:'..', plugins:[vue()], server:{port:Number(env.FRONTEND_PORT || 5177),strictPort:true,proxy:{'/api':{target:`http://127.0.0.1:${env.BACKEND_PORT || 8017}`}}} }
})
