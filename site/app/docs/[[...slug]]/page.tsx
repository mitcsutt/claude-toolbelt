// kiln-docs route v1 8cac9d04fd7a: written by @mitcsutt/kiln-docs, which updates it while it's unedited.
import { createDocsPage } from '@mitcsutt/kiln-docs/docs-page'
import { site } from '@/lib/site'

export const { generateStaticParams, generateMetadata } = site

export default createDocsPage(site)
