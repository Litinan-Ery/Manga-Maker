import type { ContractErrorPayload } from "./v03Types";

export class ContractApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, payload: ContractErrorPayload) {
    super(payload.error.message);
    this.name = "ContractApiError";
    this.status = status;
    this.code = payload.error.code;
    this.details = payload.error.details;
  }
}
