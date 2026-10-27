import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineSite } from '@mitcsutt/kiln-docs/config'

// The bin loads this file bundled in a cache folder, so `import.meta.url` isn't the site's. Both
// the bin and `next` run from the site's root.
const marketplace = JSON.parse(
  readFileSync(resolve('..', '.claude-plugin/marketplace.json'), 'utf8'),
) as { name: string; description: string }

const repository = 'https://github.com/mitcsutt/claude-toolbelt'

/** One fixed theme and the mode toggle, no picker (ADR 0046). */
export default defineSite({
  name: marketplace.name,
  description: marketplace.description,
  url: 'https://claude-toolbelt.mitchellsutton.com',
  repository: { url: repository },
  theme: { fixed: 'flightdeck' },
  nav: {
    sections: [
      { href: '/docs/plugins', label: 'Plugins' },
      { href: '/docs/reference', label: 'Reference' },
      { href: '/docs/shared-rules', label: 'Shared rules' },
      { href: '/docs/contributing', label: 'Contributing' },
    ],
  },
  footer: {
    text: 'MIT licensed.',
    links: [
      { href: repository, label: 'Source' },
      { href: '/llms.txt', label: 'llms.txt' },
    ],
  },
  search: { placeholder: 'Search the docs' },
  notFound: {
    description: 'That page isn’t here. Start from the docs.',
    action: { href: '/docs', label: 'Open the docs' },
  },
  markdown: {
    llms: {
      intro: marketplace.description,
      sections: {
        '': 'Start here',
        plugins: 'Plugins',
        reference: 'Reference',
        'shared-rules': 'Shared rules',
        contributing: 'Contributing',
      },
    },
  },
  variants: {},
})
