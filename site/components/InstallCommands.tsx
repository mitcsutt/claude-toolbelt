'use client'

import { CodeBlock } from '@mitcsutt/kiln-ui'

/** The install commands with a copy button: kiln-ui's CodeBlock has client state, so a server page can't render it. */
export function InstallCommands({ lines }: { lines: readonly string[] }) {
  return <CodeBlock code={lines.join('\n')} />
}
