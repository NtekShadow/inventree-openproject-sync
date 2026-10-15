import {
  checkPluginVersion,
  type InvenTreePluginContext
} from '@inventreedb/ui';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Progress,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';

import { installLinkify, type LinkifyConfig } from './linkify';

const API = '/plugin/openproject-sync';

type WorkPackage = {
  id: number;
  display: string;
  url: string;
  subject: string;
  status?: string;
  project?: string;
  type?: string;
  startDate?: string | null;
  dueDate?: string | null;
  percentageDone?: number | null;
};

type Ref = { key: string; text: string; url: string; wp?: WorkPackage | null };

type OrderState = {
  configured: boolean;
  can_edit: boolean;
  reference: string;
  project: string;
  linked: boolean;
  wp: WorkPackage | null;
  error?: string | null;
  warnings: string[];
  synced?: string | null;
  refs: Ref[];
};

type PanelConfig = LinkifyConfig & { is_order: boolean };

function errorMessage(error: any): string {
  return (
    error?.response?.data?.message ??
    error?.response?.data?.detail ??
    String(error)
  );
}

function WorkPackageLine({ wp }: Readonly<{ wp: WorkPackage }>) {
  return (
    <Group gap='xs' wrap='nowrap'>
      <Anchor href={wp.url} target='_blank' fw={600}>
        {wp.display}
      </Anchor>
      <Text truncate>{wp.subject}</Text>
      {wp.status && <Badge variant='light'>{wp.status}</Badge>}
    </Group>
  );
}

function RefList({ refs }: Readonly<{ refs: Ref[] }>) {
  if (!refs.length) {
    return <Text c='dimmed'>Keine OP#-Verweise gefunden.</Text>;
  }
  return (
    <Stack gap={4}>
      {refs.map((ref) =>
        ref.wp ? (
          <WorkPackageLine key={ref.key} wp={ref.wp} />
        ) : (
          <Group key={ref.key} gap='xs'>
            <Anchor href={ref.url} target='_blank'>
              {ref.text}
            </Anchor>
            <Text c='dimmed' size='sm'>
              (nicht gefunden)
            </Text>
          </Group>
        )
      )}
    </Stack>
  );
}

type ProjectOption = { value: string; label: string };

/** Searchable selection of OpenProject projects. */
function ProjectSelect({
  context,
  value,
  onChange,
  label
}: Readonly<{
  context: InvenTreePluginContext;
  value: string;
  onChange: (value: string) => void;
  label?: string;
}>) {
  const [options, setOptions] = useState<ProjectOption[]>([]);

  useEffect(() => {
    context.api
      .get(`${API}/projects/`)
      .then((res) => setOptions(res.data))
      .catch(() => setOptions([]));
  }, [context.api]);

  // Keep the configured value selectable even if it is not in the list (yet)
  const data =
    value && !options.some((o) => o.value === value)
      ? [{ value, label: value }, ...options]
      : options;

  return (
    <Select
      label={label}
      placeholder='Projekt wählen'
      searchable
      data={data}
      value={value || null}
      onChange={(v) => onChange(v ?? '')}
      miw={280}
    />
  );
}

/** Panel on pages other than orders: list the referenced work packages. */
function ReferencesPanel({
  context
}: Readonly<{ context: InvenTreePluginContext }>) {
  const [refs, setRefs] = useState<Ref[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    context.api
      .get(`${API}/refs/${context.model}/${context.id}/`)
      .then((res) => setRefs(res.data.refs))
      .catch((err) => setError(errorMessage(err)));
  }, [context.api, context.model, context.id]);

  if (error) {
    return <Alert color='red'>{error}</Alert>;
  }
  return refs === null ? <Loader size='sm' /> : <RefList refs={refs} />;
}

/** Panel on build and purchase orders. */
function OrderPanel({
  context
}: Readonly<{ context: InvenTreePluginContext }>) {
  const [state, setState] = useState<OrderState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null
  );
  const [linkRef, setLinkRef] = useState('');
  const [project, setProject] = useState('');

  const url = `${API}/order/${context.model}/${context.id}/`;

  const load = useCallback(() => {
    context.api
      .get(url)
      .then((res) => {
        setState(res.data);
        setLoadError(null);
        setProject((p) => p || res.data.project || '');
      })
      .catch((err) => setLoadError(errorMessage(err)));
  }, [context.api, url]);

  useEffect(load, [load]);

  const run = (action: string, data: Record<string, string> = {}) => {
    setBusy(action);
    setMessage(null);
    context.api
      .post(`${url}${action}/`, data)
      .then((res) => setMessage({ ok: true, text: res.data.message }))
      .catch((err) => setMessage({ ok: false, text: errorMessage(err) }))
      .finally(() => {
        setBusy(null);
        load();
        context.reloadInstance?.();
      });
  };

  if (loadError) {
    return <Alert color='red'>{loadError}</Alert>;
  }
  if (!state) {
    return <Loader size='sm' />;
  }
  if (!state.configured) {
    return (
      <Alert color='yellow' title='Nicht eingerichtet'>
        OpenProject-URL und API-Schlüssel in den Plugin-Einstellungen
        (OpenProject Sync) eintragen.
      </Alert>
    );
  }

  const wp = state.wp;

  return (
    <Stack gap='md'>
      {message && (
        <Alert
          color={message.ok ? 'green' : 'red'}
          withCloseButton
          onClose={() => setMessage(null)}
        >
          {message.text}
        </Alert>
      )}
      {state.error && (
        <Alert color='red' title='Letzter Sync fehlgeschlagen'>
          {state.error}
        </Alert>
      )}
      {state.warnings.length > 0 && (
        <Alert color='yellow' title='Hinweise'>
          {state.warnings.map((w) => (
            <Text key={w} size='sm'>
              {w}
            </Text>
          ))}
        </Alert>
      )}

      {state.linked && wp && (
        <Paper withBorder p='md'>
          <Stack gap='sm'>
            <WorkPackageLine wp={wp} />
            <Table variant='vertical' withTableBorder={false}>
              <Table.Tbody>
                <Table.Tr>
                  <Table.Th w={160}>Projekt</Table.Th>
                  <Table.Td>{wp.project}</Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Th>Typ</Table.Th>
                  <Table.Td>{wp.type}</Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Th>Zeitraum</Table.Th>
                  <Table.Td>
                    {wp.startDate ?? '–'} bis {wp.dueDate ?? '–'}
                  </Table.Td>
                </Table.Tr>
                {wp.percentageDone != null && (
                  <Table.Tr>
                    <Table.Th>Erledigt</Table.Th>
                    <Table.Td>
                      <Progress value={wp.percentageDone} size='lg' />
                    </Table.Td>
                  </Table.Tr>
                )}
                <Table.Tr>
                  <Table.Th>Letzter Sync</Table.Th>
                  <Table.Td>
                    {state.synced
                      ? new Date(state.synced).toLocaleString()
                      : '–'}
                  </Table.Td>
                </Table.Tr>
              </Table.Tbody>
            </Table>
            {state.can_edit && (
              <Group>
                <Button loading={busy === 'sync'} onClick={() => run('sync')}>
                  Jetzt synchronisieren
                </Button>
                <Button
                  variant='subtle'
                  color='red'
                  loading={busy === 'unlink'}
                  onClick={() => run('unlink')}
                >
                  Verknüpfung lösen
                </Button>
              </Group>
            )}
            {state.can_edit && (
              <Group align='end'>
                <ProjectSelect
                  context={context}
                  label='In anderes Projekt verschieben'
                  value={project}
                  onChange={setProject}
                />
                <Button
                  variant='light'
                  loading={busy === 'move'}
                  disabled={!project}
                  onClick={() => run('move', { project })}
                >
                  Verschieben
                </Button>
              </Group>
            )}
          </Stack>
        </Paper>
      )}

      {state.linked && !wp && (
        <Alert color='red' title='Arbeitspaket nicht gefunden'>
          <Stack gap='xs'>
            <Text size='sm'>
              Das verknüpfte Arbeitspaket gibt es in OpenProject nicht mehr oder
              es ist nicht sichtbar. Beim nächsten Sync wird die Verknüpfung
              entfernt und, wenn automatisches Anlegen an ist, ein neues
              Arbeitspaket angelegt.
            </Text>
            {state.can_edit && (
              <Group>
                <Button
                  size='xs'
                  loading={busy === 'sync'}
                  onClick={() => run('sync')}
                >
                  Jetzt synchronisieren
                </Button>
                <Button
                  size='xs'
                  variant='subtle'
                  color='red'
                  loading={busy === 'unlink'}
                  onClick={() => run('unlink')}
                >
                  Verknüpfung lösen
                </Button>
              </Group>
            )}
          </Stack>
        </Alert>
      )}

      {!state.linked && (
        <Paper withBorder p='md'>
          <Stack gap='md'>
            <Text>
              {state.reference} ist noch nicht mit OpenProject verknüpft.
            </Text>
            {state.can_edit ? (
              <>
                <Stack gap='xs'>
                  <Title order={6}>Neues Arbeitspaket anlegen</Title>
                  <Group align='end'>
                    <ProjectSelect
                      context={context}
                      label='Projekt'
                      value={project}
                      onChange={setProject}
                    />
                    <Button
                      loading={busy === 'create'}
                      disabled={!project}
                      onClick={() => run('create', { project })}
                    >
                      Anlegen
                    </Button>
                  </Group>
                </Stack>
                <Stack gap='xs'>
                  <Title order={6}>
                    Mit bestehendem Arbeitspaket verknüpfen
                  </Title>
                  <Group align='end'>
                    <TextInput
                      placeholder={`${context.context?.prefix ?? 'OP#'}EL-5 oder Link`}
                      value={linkRef}
                      onChange={(e) => setLinkRef(e.currentTarget.value)}
                    />
                    <Button
                      variant='light'
                      loading={busy === 'link'}
                      disabled={!linkRef}
                      onClick={() => run('link', { ref: linkRef })}
                    >
                      Verknüpfen
                    </Button>
                  </Group>
                </Stack>
              </>
            ) : (
              <Text c='dimmed'>Keine Berechtigung zum Verknüpfen.</Text>
            )}
          </Stack>
        </Paper>
      )}

      <Stack gap='xs'>
        <Title order={6}>Verweise im Auftrag (als Relation verknüpft)</Title>
        <RefList refs={state.refs} />
      </Stack>
    </Stack>
  );
}

// Called by InvenTree to render the panel
export function renderPanel(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  const config = context.context as PanelConfig;
  installLinkify(config);
  return config?.is_order ? (
    <OrderPanel context={context} />
  ) : (
    <ReferencesPanel context={context} />
  );
}
