import { api } from "./api";

export interface EdgeToolCatalog {
  supported: string[];
}

export function listEdgeTools(): Promise<EdgeToolCatalog> {
  return api.get<EdgeToolCatalog>("/v1/edge/tool-config");
}
