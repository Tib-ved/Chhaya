import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://127.0.0.1:8080'
  const wsTarget = backendUrl.replace(/^http/, 'ws')

  return {
    plugins: [react()],
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: backendUrl,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: 'localhost',
          headers: {
            ...(env.CHHAYA_SESSION ? { Cookie: `chhaya_session=${env.CHHAYA_SESSION}` } : {}),
            ...(env.INTERNAL_SECRET ? { 'X-Internal-Secret': env.INTERNAL_SECRET } : {}),
          },
        },
        '/auth': {
          target: backendUrl,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: 'localhost',
        },
        '/login': {
          target: backendUrl,
          changeOrigin: true,
          secure: false,
        },
        '/admin': {
          target: backendUrl,
          changeOrigin: true,
          secure: false,
          cookieDomainRewrite: 'localhost',
          headers: {
            ...(env.CHHAYA_SESSION ? { Cookie: `chhaya_session=${env.CHHAYA_SESSION}` } : {}),
            ...(env.INTERNAL_SECRET ? { 'X-Internal-Secret': env.INTERNAL_SECRET } : {}),
          },
        },
        '/internal': {
          target: backendUrl,
          changeOrigin: true,
          secure: false,
          headers: {
            ...(env.INTERNAL_SECRET ? { 'X-Internal-Secret': env.INTERNAL_SECRET } : {}),
          },
        },
        '/browser': {
          target: wsTarget,
          ws: true,
          changeOrigin: true,
          secure: false,
          headers: {
            ...(env.CHHAYA_SESSION ? { Cookie: `chhaya_session=${env.CHHAYA_SESSION}` } : {}),
          },
        },
      },
    },
  }
})
