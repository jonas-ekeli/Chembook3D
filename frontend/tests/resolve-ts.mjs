// Loaded before the unit tests (`node --import ./tests/resolve-ts.mjs`): the app's modules import
// each other without an extension ("./api"), as Vite allows, so an import Node cannot find is
// tried again as "./api.ts". Node strips the types itself. Nothing is mocked.
import { registerHooks } from 'node:module'

registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context)
    } catch (error) {
      if (error?.code !== 'ERR_MODULE_NOT_FOUND' || !/^\.{1,2}\//.test(specifier) || /\.[cm]?[jt]sx?$/.test(specifier))
        throw error
      return next(`${specifier}.ts`, context)
    }
  },
})
