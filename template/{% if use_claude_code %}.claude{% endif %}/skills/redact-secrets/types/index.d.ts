// the session's switch (/redact off) and how many secrets were masked so far
export type RedactCount = number

declare module 'claude-code' {
  interface PluginState {
    'redact-secrets': { isOff: boolean; masked: RedactCount }
  }
}
