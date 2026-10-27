import { createDocsSite } from '@mitcsutt/kiln-docs'
import { docs, generated } from 'fumadocs-mdx:collections/server'
import config from '../site.config'

export const site = createDocsSite({ config, collections: { docs, generated } })
