/// <reference types="vite/client" />

/**
 * Build-time configuration reaching the bundle through Vite's `import.meta.env`.
 *
 * Declared explicitly rather than left to the `vite/client` catch-all so that a
 * misspelled variable is a type error instead of `undefined` silently falling
 * back to localhost -- which is what shipped: the desktop shell read a
 * `globalThis.__API_URL__` that nothing ever assigned, so every installed till
 * pointed at its own machine no matter how the build was configured.
 */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
