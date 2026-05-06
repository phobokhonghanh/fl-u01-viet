import { defineConfig } from 'vite';

export default defineConfig({
    // For GitHub Pages: set base to '/<repo-name>/'
    // This is controlled via env var so local dev uses '/' and CI uses the repo path
    base: process.env.VITE_BASE_PATH || '/',

    build: {
        outDir: 'dist',
        // Generate sourcemaps for easier debugging
        sourcemap: false,
        // Optimize chunk size
        rollupOptions: {
            input: {
                // main: 'index.html',
                admin: 'admin.html',
                payment: 'payment.html',
                paymentAdmin: 'payment-admin.html',
            },
            output: {
                manualChunks: undefined,
            },
        },
    },

    server: {
        port: 3000,
        open: true,
        host: true,
    },
});
