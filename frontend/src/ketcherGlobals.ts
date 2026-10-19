// Some of Ketcher's dependencies expect Node's `global`; imported before Ketcher (D142).
const scope = globalThis as typeof globalThis & { global?: typeof globalThis }
scope.global ??= globalThis
