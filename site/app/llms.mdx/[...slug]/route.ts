// kiln-docs route v1 a7bdaec79af1: written by @mitcsutt/kiln-docs, which updates it while it's unedited.
import { site } from '@/lib/site'

export const dynamic = 'force-static'

export const { GET, generateStaticParams } = site.routes.pageMarkdown
