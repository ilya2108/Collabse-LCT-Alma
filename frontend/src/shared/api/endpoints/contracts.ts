import type { RegistryFetchParams } from '@/components/RegistryTable/types';
import { api } from '../client';
import type { Contract, ContractStatus, FileObject, ListEnvelope } from '../types';

/** Договоры и их файлы в MinIO (api-contract.md §3.2). */

export function listContracts(params: RegistryFetchParams): Promise<ListEnvelope<Contract>> {
  return api.get<ListEnvelope<Contract>>('/contracts', {
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

export function listContractsByUniversity(
  universityId: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<Contract>> {
  return api.get<ListEnvelope<Contract>>('/contracts', {
    query: { university_id: universityId, limit: 50 },
    signal,
  });
}

export function getContract(id: string, signal?: AbortSignal): Promise<Contract> {
  return api.get<Contract>(`/contracts/${id}`, { signal });
}

export interface ContractPayload {
  number: string;
  university_id?: string | null;
  counterparty_id?: string | null;
  status?: ContractStatus;
  signed_at?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  amount?: string | null;
  product_ids?: string[];
  notes?: string | null;
}

export function createContract(payload: ContractPayload): Promise<Contract> {
  return api.post<Contract>('/contracts', { body: payload });
}

export function updateContract(
  id: string,
  patch: Partial<ContractPayload> & { version: number },
): Promise<Contract> {
  return api.patch<Contract>(`/contracts/${id}`, { body: patch });
}

// --- Файлы договора ----------------------------------------------------------

export function listContractFiles(
  contractId: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<FileObject>> {
  return api.get<ListEnvelope<FileObject>>(`/contracts/${contractId}/files`, { signal });
}

export function uploadContractFile(contractId: string, file: File): Promise<FileObject> {
  const form = new FormData();
  form.append('file', file);
  return api.post<FileObject>(`/contracts/${contractId}/files`, { body: form });
}

export function deleteContractFile(contractId: string, fileId: string): Promise<void> {
  return api.delete<void>(`/contracts/${contractId}/files/${fileId}`);
}
