/// <reference types="vitest/config" />
import { configDefaults } from 'vitest/config'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

const bootstrapScripts = new Set<string>()

// https://vite.dev/config/
export default defineConfig({
  build: {
    license: { fileName: 'THIRD_PARTY_LICENSES.md' },
  },
  server: {
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      }
    }
  },
  plugins: [
    react(),
    {
      name: 'precache-bootstrap-scripts',
      generateBundle(_options, bundle) {
        bootstrapScripts.clear()
        const visit = (fileName: string) => {
          if (bootstrapScripts.has(fileName)) return
          const chunk = bundle[fileName]
          if (chunk?.type !== 'chunk') return
          bootstrapScripts.add(fileName)
          chunk.imports.forEach(visit)
        }
        for (const chunk of Object.values(bundle)) {
          if (chunk.type === 'chunk' && (chunk.isEntry || chunk.name.startsWith('workbox-window'))) visit(chunk.fileName)
        }
      },
    },
    VitePWA({
      registerType: 'autoUpdate',
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2,txt,md}'],
        // Cache route scripts when visited, not eagerly during worker installation.
        manifestTransforms: [async entries => ({
          manifest: entries.filter(entry => !entry.url.endsWith('.js') || !entry.url.startsWith('assets/') || bootstrapScripts.has(entry.url)),
          warnings: [],
        })],
        runtimeCaching: [{
          urlPattern: ({ request, sameOrigin, url }) => sameOrigin && (
            request.destination === 'script'
            // Workbox CACHE_URLS builds a GET without a script destination.
            || (request.destination === '' && url.href === url.origin + url.pathname && /^\/assets\/[^/]+-[\w-]{8}\.js$/.test(url.pathname))
          ),
          handler: 'CacheFirst',
          options: { cacheName: 'scan64-route-scripts', expiration: { maxEntries: 64 } },
        }],
        navigateFallbackDenylist: [/^\/(?:v1|health|docs|openapi\.json|redoc)(?:[/?#]|$)/],
      },
      includeAssets: ['favicon.svg', 'logo.svg', 'pwa-192x192.png', 'pwa-512x512.png', 'pwa-maskable-512x512.png', 'apple-touch-icon.png', 'licenses.html'],
      manifest: {
        name: 'Scan64',
        short_name: 'Scan64',
        description: 'Offline-capable chess training application',
        theme_color: '#ffffff',
        background_color: '#ffffff',
        icons: [
          {
            src: 'favicon.svg',
            sizes: 'any',
            type: 'image/svg+xml'
          },
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png'
          },
          {
            src: 'pwa-maskable-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable'
          }
        ]
      }
    })
  ],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    globals: true,
    exclude: [...configDefaults.exclude, 'tests/e2e/**']
  }
})
