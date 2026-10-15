import {
  checkPluginVersion,
  type InvenTreePluginContext
} from '@inventreedb/ui';
import {
  Alert,
  Anchor,
  Button,
  Group,
  Loader,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';

import { installLinkify, type LinkifyConfig } from './linkify';

const API = '/plugin/openproject-sync/overview/';

const ORDER_PATHS: Record<string, string> = {
  build: '/manufacturing/build-order',
  purchaseorder: '/purchasing/purchase-order'
};

type Overview = {
  configured: boolean;
  connection: string | null;
  types: { model_type: string; label: string; open: number; linked: number }[];
  errors: {
    reference: string;
    model_type?: string;
    id?: number;
    error: string;
  }[];
};

function OpenProjectDashboardItem({
  context
}: Readonly<{ context: InvenTreePluginContext }>) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    context.api
      .get(API)
      .then((res) => setData(res.data))
      .catch((err) => setError(err?.response?.data?.detail ?? String(err)));
  }, [context.api]);

  useEffect(load, [load]);

  const syncAll = () => {
    setBusy(true);
    context.api
      .post(API)
      .then((res) => setMessage(res.data.message))
      .catch((err) => setMessage(err?.response?.data?.detail ?? String(err)))
      .finally(() => {
        setBusy(false);
        load();
      });
  };

  if (error) {
    return <Alert color='red'>{error}</Alert>;
  }
  if (!data) {
    return <Loader size='sm' />;
  }
  if (!data.configured) {
    return (
      <Alert color='yellow'>
        OpenProject ist in den Plugin-Einstellungen noch nicht eingerichtet.
      </Alert>
    );
  }

  return (
    <Stack gap='xs'>
      <Text size='sm' c='dimmed'>
        {data.connection
          ? `Verbunden als ${data.connection}`
          : 'Keine Verbindung'}
      </Text>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Offene Aufträge</Table.Th>
            <Table.Th>verknüpft</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.types.map((t) => (
            <Table.Tr key={t.model_type}>
              <Table.Td>{t.label}</Table.Td>
              <Table.Td>
                {t.linked} / {t.open}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {data.errors.map((e) => (
        <Alert key={`${e.model_type}-${e.reference}`} color='red' p='xs'>
          {e.model_type && e.id ? (
            <Anchor
              onClick={() =>
                context.navigate(`${ORDER_PATHS[e.model_type ?? '']}/${e.id}`)
              }
            >
              {e.reference}
            </Anchor>
          ) : (
            e.reference
          )}
          : {e.error}
        </Alert>
      ))}
      {message && <Text size='sm'>{message}</Text>}
      {context.user?.isStaff?.() && (
        <Group>
          <Button size='xs' variant='light' loading={busy} onClick={syncAll}>
            Alle offenen Aufträge synchronisieren
          </Button>
        </Group>
      )}
    </Stack>
  );
}

// Called by InvenTree to render the dashboard item
export function renderDashboardItem(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  installLinkify(context.context as LinkifyConfig);
  return <OpenProjectDashboardItem context={context} />;
}
