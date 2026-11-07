import type { DataProvider } from 'react-admin';
import { fetchUtils } from 'react-admin';
import { config } from './config';
import { getJwtToken } from './utils/auth';

// Map resource names to their primary key fields
const primaryKeyMap: Record<string, string> = {
  'orders': 'order_id',
  'intakes': 'intake_id',
  'payment-tiers': 'tier_key',
  'eligibility-rules': 'state',
  'email-templates': 'template_id',
  'email-suppressions': 'email',
  'tracking': 'tracking_id',
  'tracking-jobs': 'job_id',
  'honeybee-events': 'event_id',
  'settings': 'config_key',
};

const getPrimaryKey = (resource: string): string => primaryKeyMap[resource] || 'id';

const httpClient = (url: string, options: any = {}) => {
  const headers = new Headers(options.headers || { Accept: 'application/json' });
  const token = getJwtToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  return fetchUtils.fetchJson(url, { ...options, headers });
};

/** Authenticated fetch for endpoints outside the generic CRUD contract (returns raw Response). */
export const authFetch = async (url: string, options: RequestInit = {}) => {
  const token = getJwtToken();
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
};

const withId = (resource: string, item: any) => ({ ...item, id: item[getPrimaryKey(resource)] });

// Client-side filtering: exact match per filter key (the admin API returns
// the whole table; tables are small).
const applyFilter = (items: any[], filter: Record<string, any> = {}) =>
  items.filter(item =>
    Object.entries(filter).every(([key, value]) => {
      if (value === undefined || value === null || value === '') return true;
      if (key === 'q') {
        const needle = String(value).toLowerCase();
        return Object.values(item).some(v => String(v ?? '').toLowerCase().includes(needle));
      }
      return String(item[key] ?? '') === String(value);
    })
  );

export const dataProvider: DataProvider = {
  getList: async (resource, params) => {
    const { page, perPage } = params.pagination ?? { page: 1, perPage: 25 };
    const { field, order } = params.sort ?? { field: getPrimaryKey(resource), order: 'ASC' };

    const { json } = await httpClient(`${config.apiUrl}/${resource}`);
    // The Lambda returns { items: [...], count: N }
    const items = applyFilter(json.items || [], params.filter);

    const sortedItems = [...items].sort((a: any, b: any) => {
      if (!a[field] && !b[field]) return 0;
      if (!a[field]) return 1;
      if (!b[field]) return -1;
      if (order === 'ASC') return a[field] > b[field] ? 1 : -1;
      return a[field] < b[field] ? 1 : -1;
    });

    const start = (page - 1) * perPage;
    const paginatedItems = sortedItems.slice(start, start + perPage);

    return {
      data: paginatedItems.map((item: any) => withId(resource, item)),
      total: items.length,
    };
  },

  getOne: async (resource, params) => {
    const { json } = await httpClient(`${config.apiUrl}/${resource}/${encodeURIComponent(params.id)}`);
    return { data: withId(resource, json) };
  },

  create: async (resource, params) => {
    const { json } = await httpClient(`${config.apiUrl}/${resource}`, {
      method: 'POST',
      body: JSON.stringify(params.data),
    });
    return { data: withId(resource, json) };
  },

  update: async (resource, params) => {
    const { json } = await httpClient(`${config.apiUrl}/${resource}/${encodeURIComponent(params.id)}`, {
      method: 'PUT',
      body: JSON.stringify(params.data),
    });
    return { data: withId(resource, json) };
  },

  delete: async (resource, params) => {
    await httpClient(`${config.apiUrl}/${resource}/${encodeURIComponent(params.id)}`, { method: 'DELETE' });
    return { data: params.previousData as any };
  },

  getMany: async (resource, params) => {
    const { json } = await httpClient(`${config.apiUrl}/${resource}`);
    const primaryKey = getPrimaryKey(resource);
    const items = (json.items || []).filter((item: any) => params.ids.includes(item[primaryKey]));
    return { data: items.map((item: any) => withId(resource, item)) };
  },

  getManyReference: async (resource, params) => {
    const { json } = await httpClient(`${config.apiUrl}/${resource}`);
    const items = (json.items || []).filter((item: any) => item[params.target] === params.id);
    return { data: items.map((item: any) => withId(resource, item)), total: items.length };
  },

  updateMany: async (resource, params) => {
    await Promise.all(
      params.ids.map(id =>
        httpClient(`${config.apiUrl}/${resource}/${encodeURIComponent(id)}`, {
          method: 'PUT',
          body: JSON.stringify(params.data),
        })
      )
    );
    return { data: params.ids };
  },

  deleteMany: async (resource, params) => {
    await Promise.all(
      params.ids.map(id =>
        httpClient(`${config.apiUrl}/${resource}/${encodeURIComponent(id)}`, { method: 'DELETE' })
      )
    );
    return { data: params.ids };
  },
};

// ---------------------------------------------------------------------------
// Sub-resource actions (POST /{table}/{action} or /{table}/{id}/{action})
// ---------------------------------------------------------------------------

/** POST a JSON body to a sub-resource path under the admin API; returns parsed JSON. */
export const postAction = async <T = any>(path: string, body: Record<string, unknown> = {}): Promise<T> => {
  const { json } = await httpClient(`${config.apiUrl}/${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return json as T;
};

/**
 * Export selected orders: marks them EXPORTED server-side and returns the CSV
 * in the vendor's format. Response: { export_batch_id, exported_count, csv }.
 */
export const exportOrdersBatch = (orderIds: string[]) =>
  postAction<{ export_batch_id: string; exported_count: number; exported_at: string; csv: string }>(
    'orders/export-batch',
    { order_ids: orderIds }
  );

export const markOrderPaid = (orderId: string, amount: number) =>
  postAction(`orders/${encodeURIComponent(orderId)}/mark-paid`, { amount });

export const cancelOrder = (orderId: string, reason?: string) =>
  postAction(`orders/${encodeURIComponent(orderId)}/cancel`, reason ? { reason } : {});

export const resubmitOrderToNectar = (orderId: string) =>
  postAction(`orders/${encodeURIComponent(orderId)}/resubmit-nectar`);

export const markOrderShipped = (orderId: string, trackingNumber: string, carrier?: string) =>
  postAction(`orders/${encodeURIComponent(orderId)}/mark-shipped`, {
    tracking_number: trackingNumber,
    ...(carrier ? { carrier } : {}),
  });
