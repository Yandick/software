import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  const backendHost = process.env.BACKEND_HOST || '127.0.0.1';
  const backendPort = process.env.BACKEND_PORT || '8010';
  const backendTarget =
    process.env.VITE_BACKEND_PROXY_TARGET ||
    `http://${backendHost}:${backendPort}/api`;

  return {
    application: {},
    vite: {
      server: {
        proxy: {
          '/api': {
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
            target: backendTarget,
            ws: true,
          },
        },
      },
    },
  };
});
