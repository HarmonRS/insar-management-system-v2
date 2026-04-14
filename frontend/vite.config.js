import path from 'path'
import { fileURLToPath } from 'url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const frontendRoot = __dirname
  const projectRoot = path.resolve(frontendRoot, '..')
  const env = {
    ...loadEnv(mode, frontendRoot, ''),
    ...loadEnv(mode, projectRoot, ''),
  }
  const backendTarget = env.VITE_BACKEND_TARGET || 'http://localhost:18000'
  const tileServerUrl = env.VITE_TILE_SERVER_URL || ''
  const tileServerToken = env.VITE_TILE_SERVER_TOKEN || ''
  const manualChunks = (id) => {
    if (!id.includes('node_modules')) {
      return
    }

    if (id.includes('leaflet')) {
      return 'leaflet-vendor'
    }

    if (id.includes('chart.js') || id.includes('react-chartjs-2') || id.includes('chartjs-adapter-date-fns') || id.includes('date-fns')) {
      return 'charts-vendor'
    }

    if (id.includes('react-markdown')) {
      return 'markdown-vendor'
    }

    if (id.includes('axios')) {
      return 'network-vendor'
    }

    if (id.includes('flatpickr')) {
      return 'picker-vendor'
    }

    if (id.includes('html2canvas')) {
      return 'html2canvas-vendor'
    }

    if (id.includes('react') || id.includes('scheduler') || id.includes('zustand')) {
      return 'framework-vendor'
    }

    return 'vendor'
  }

  return {
    plugins: [react()],
    define: {
      'import.meta.env.VITE_TILE_SERVER_URL': JSON.stringify(tileServerUrl),
      'import.meta.env.VITE_TILE_SERVER_TOKEN': JSON.stringify(tileServerToken),
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks,
        },
      },
    },
    server: {
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
        }
      }
    }
  }
})
