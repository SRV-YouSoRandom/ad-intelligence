import useSWR from 'swr';

export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

export interface FetchError extends Error {
  info?: unknown;
  status?: number;
}

export const fetcher = async (url: string) => {
  const res = await fetch(`${API_BASE_URL}${url}`);
  if (!res.ok) {
    const error: FetchError = new Error('An error occurred while fetching the data.');
    error.info = await res.json().catch(() => ({}));
    error.status = res.status;
    throw error;
  }
  return res.json();
};

export const fetchApi = async (url: string, options?: RequestInit) => {
  const res = await fetch(`${API_BASE_URL}${url}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg = (body as { detail?: string }).detail || `API error: ${res.status}`;
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
};

// ── SWR Hooks ──────────────────────────────────────────────────────────────────

export const useBrands = () => {
  const { data, error, isLoading, mutate } = useSWR('/brands', fetcher);
  return { brands: data?.brands || [], total: data?.total || 0, isLoading, error, mutate };
};

export const useAds = (params: Record<string, string | number | boolean | null | undefined>) => {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.append(key, String(value));
  });
  const endpoint = `/ads?${query.toString()}`;
  const { data, error, isLoading, mutate } = useSWR(endpoint, fetcher);
  return { ads: data?.ads || [], total: data?.total || 0, isLoading, error, mutate };
};

export const useAdDetail = (adId: string | undefined) => {
  const { data, error, isLoading, mutate } = useSWR(adId ? `/ads/${adId}` : null, fetcher);
  return { ad: data, isLoading, error, mutate };
};

export const useInsights = (adId: string | undefined) => {
  const { data, error, isLoading, mutate } = useSWR(adId ? `/ads/${adId}/insights` : null, fetcher);
  return { insight: data, isLoading, error, mutate };
};

export const useJobPoll = (jobId: string | null, interval = 3000) => {
  const { data, error, mutate } = useSWR(
    jobId ? `/jobs/${jobId}/status` : null,
    fetcher,
    { refreshInterval: (data) => (data?.status === 'DONE' || data?.status === 'FAILED') ? 0 : interval }
  );
  return {
    job: data,
    isPolling: !!jobId && data?.status !== 'DONE' && data?.status !== 'FAILED',
    error,
    mutate,
  };
};

// ── API calls ──────────────────────────────────────────────────────────────────

export const searchBrand = async (payload: {
  identifier: string;
  identifier_type: string;
  countries: string[];
  ad_active_status: string;
  max_ads?: number;
}) => fetchApi('/brands/search', { method: 'POST', body: JSON.stringify(payload) });

export const generateInsight = async (adId: string) =>
  fetchApi(`/ads/${adId}/insights/generate`, { method: 'POST' });

export const deleteInsight = async (adId: string) =>
  fetchApi(`/ads/${adId}/insights`, { method: 'DELETE' });

export const getBrandRecommendations = async (brandId: string) =>
  fetchApi(`/brands/${brandId}/recommendations`);