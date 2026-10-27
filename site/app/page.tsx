import { Button, Card, Container, Grid, Heading, Inline, Stack, Tag, Text } from '@mitcsutt/kiln-ui'
import { Shell } from '@mitcsutt/kiln-docs/shell'
import Link from 'next/link'
import landing from '../.generated/landing.json'
import { InstallCommands } from '@/components/InstallCommands'
import config from '../site.config'

/** The landing page at `/`, the site's own. Its copy and its plugin cards come from the repo, through the generator. */
export default function HomePage() {
  return (
    <Shell>
      <Container>
        <Stack gap={8}>
          <Stack gap={4}>
            <Heading level={1}>{config.name}</Heading>
            <Text size="lg">{landing.tagline}</Text>
            <Inline gap={3}>
              <Button asChild>
                <Link href="/docs">Read the docs</Link>
              </Button>
              <Button asChild variant="outline">
                <a href={config.repository.url}>View on GitHub</a>
              </Button>
            </Inline>
          </Stack>
          <Stack gap={3}>
            <Heading level={2}>Install</Heading>
            <InstallCommands lines={landing.install} />
          </Stack>
          <Stack gap={4}>
            <Heading level={2}>Plugins</Heading>
            <Grid minItemWidth="md" gap={4}>
              {landing.plugins.map((plugin) => (
                <Card key={plugin.name} asChild>
                  <Link href={plugin.href}>
                    <Stack gap={2}>
                      <Card.Title>{plugin.name}</Card.Title>
                      <Card.Description>{plugin.summary}</Card.Description>
                      <Inline gap={2}>
                        <Tag>{plugin.version}</Tag>
                        <Tag>{plugin.category}</Tag>
                      </Inline>
                    </Stack>
                  </Link>
                </Card>
              ))}
            </Grid>
          </Stack>
        </Stack>
      </Container>
    </Shell>
  )
}
