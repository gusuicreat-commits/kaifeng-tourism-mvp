import path from 'node:path'

// Playwright's installed Chromium is the default on every OS.
export const browserLaunchOptions = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}
  : {}

export function testServerCommand(script:string) {
  const python=path.resolve('..','.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python')
  const server=path.resolve('..','scripts',script)
  return `"${python}" "${server}"`
}
