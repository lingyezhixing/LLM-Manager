import type { components } from "@/api/types";

export type ModelInfo = components["schemas"]["ModelInfo"];
export type ModelsResponse = components["schemas"]["ModelsResponse"];

export async function fetchModels(): Promise<ModelsResponse> {
  const res = await fetch("/api/models");
  if (!res.ok) throw new Error(`/api/models failed: ${res.status}`);
  return (await res.json()) as ModelsResponse;
}
