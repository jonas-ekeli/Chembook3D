// The binaryWasm build of ketcher-standalone (Indigo as a separate .wasm file, D142) ships its
// types beside it but does not name them in its package exports.
declare module 'ketcher-standalone/dist/binaryWasm' {
  export { StandaloneStructServiceProvider } from 'ketcher-standalone'
}
