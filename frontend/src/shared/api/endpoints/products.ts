import type { RegistryFetchParams } from '@/components/RegistryTable/types';
import { api } from '../client';
import type { ListEnvelope, Product } from '../types';

/** Продукты компании (api-contract.md §3.3). */

export function listProducts(params: RegistryFetchParams): Promise<ListEnvelope<Product>> {
  return api.get<ListEnvelope<Product>>('/products', {
    query: {
      limit: params.limit,
      offset: params.offset,
      sort: params.sort,
      search: params.search,
      ...params.filters,
    },
    signal: params.signal,
  });
}

/** Все активные продукты для селектов/тегов (продуктов немного). */
export function listActiveProducts(signal?: AbortSignal): Promise<ListEnvelope<Product>> {
  return api.get<ListEnvelope<Product>>('/products', {
    query: { limit: 200, is_active: 'true' },
    signal,
  });
}

export function getProduct(id: string, signal?: AbortSignal): Promise<Product> {
  return api.get<Product>(`/products/${id}`, { signal });
}

export interface ProductPayload {
  name: string;
  code: string;
  product_type?: string;
  description?: string | null;
  is_active?: boolean;
}

export function createProduct(payload: ProductPayload): Promise<Product> {
  return api.post<Product>('/products', { body: payload });
}

export function updateProduct(
  id: string,
  patch: Partial<ProductPayload> & { version: number },
): Promise<Product> {
  return api.patch<Product>(`/products/${id}`, { body: patch });
}
