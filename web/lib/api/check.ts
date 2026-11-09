import type { components } from "./schema";

// Fails to compile if the generated schema stops matching the API's health contract.
export const healthExample: components["schemas"]["HealthResponse"] = {
  status: "ok",
  version: "0.1.0",
};
export const readyExample: components["schemas"]["ReadyResponse"] = {
  status: "ready",
};
