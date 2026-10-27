// Rewrites the repo-relative links in a source file's Markdown to the site's routes, so a README
// keeps linking the way it does on GitHub. A target that isn't a page goes to GitHub; one that
// resolves to nothing is an error, collected for the caller to report.
import { existsSync, statSync } from 'node:fs'
import { posix, resolve } from 'node:path'
import { mapOutsideFences } from './markdown.ts'

export interface LinkOptions {
  /** The repo's root on disk. */
  root: string
  /** The repo's GitHub URL, with no trailing slash. */
  repoUrl: string
  /** The branch GitHub links point at. */
  branch: string
  /** Repo path of a source file → the route of the page that renders it. */
  routes: Readonly<Record<string, string>>
  /** `path#anchor` → the route of the page that took that section, when it isn't the file's own. */
  anchorRoutes?: Readonly<Record<string, string>>
}

export interface LinkContext {
  /** The repo path of the file the Markdown came from. */
  source: string
  /** The route of the page it ends up on. */
  route: string
  /** Same-file anchors whose heading the page drops (its title): they link to the page itself. */
  droppedAnchors?: ReadonlySet<string>
}

const LINK = /\]\(\s*(<[^>]+>|[^)\s]+)(\s+"[^"]*")?\s*\)/g
const SCHEME = /^[a-z][a-z0-9+.-]*:/i

export function createLinkRewriter(options: LinkOptions) {
  const errors: string[] = []

  function target(href: string, context: LinkContext): string {
    // A scheme is an external link, and a leading slash is a site route the generator already wrote.
    if (SCHEME.test(href) || href.startsWith('/')) return href
    const [rawPath = '', fragment] = href.split('#', 2)
    const suffix = fragment === undefined ? '' : `#${fragment}`
    if (rawPath === '') {
      return fragment !== undefined && context.droppedAnchors?.has(fragment) ? context.route : href
    }
    const path = posix.normalize(posix.join(posix.dirname(context.source), rawPath)).replace(/\/$/, '')
    if (path.startsWith('..')) {
      errors.push(`${context.source}: ${href} points outside the repo`)
      return href
    }
    const onDisk = resolve(options.root, path)
    const route = options.routes[path] ?? options.routes[`${path}/README.md`]
    if (route !== undefined) {
      return options.anchorRoutes?.[`${path}${suffix}`] ?? `${route}${suffix}`
    }
    if (!existsSync(onDisk)) {
      errors.push(`${context.source}: ${href} resolves to ${path}, which doesn't exist`)
      return href
    }
    const kind = statSync(onDisk).isDirectory() ? 'tree' : 'blob'
    return `${options.repoUrl}/${kind}/${options.branch}/${path}${suffix}`
  }

  function rewrite(markdown: string, context: LinkContext): string {
    return mapOutsideFences(markdown, (line) =>
      // Odd-numbered parts are code spans, which link syntax inside stays literal.
      line
        .split(/(`+[^`]*`+)/)
        .map((part, index) =>
          index % 2 === 1
            ? part
            : part.replace(LINK, (_, href: string, title: string | undefined) => {
                const bare = href.startsWith('<') ? href.slice(1, -1) : href
                return `](${target(bare, context)}${title ?? ''})`
              }),
        )
        .join(''),
    )
  }

  /** A GitHub link for a repo path, for the generated pages' "Source" lines. */
  function github(path: string, kind: 'blob' | 'tree' = 'blob'): string {
    return `${options.repoUrl}/${kind}/${options.branch}/${path}`
  }

  return { rewrite, github, errors }
}
