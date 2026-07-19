import { api, buildQuery } from "../lib/apiClient";
import type {
  CallDirection,
  CallDisposition,
  CallRecordCreate,
  CallRecordRead,
  CallRecordResponse,
} from "../lib/types";

export interface ListCallRecordsParams {
  direction?: CallDirection;
  disposition?: CallDisposition;
  tool_name?: string;
  campaign_name?: string;
  limit?: number; // 1–100, default 50
  offset?: number;
}

export function listCallRecords(params: ListCallRecordsParams = {}) {
  return api.get<CallRecordRead[]>(`/call-records${buildQuery({ ...params })}`);
}

export function getCallRecord(externalCallId: string) {
  return api.get<CallRecordRead>(
    `/call-records/${encodeURIComponent(externalCallId)}`,
  );
}

/** Manual creation (normally the webhooks do this). 409 if the id exists. */
export function createCallRecord(body: CallRecordCreate) {
  return api.post<CallRecordResponse>("/call-records", body);
}
