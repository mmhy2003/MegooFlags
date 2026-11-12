import { api, type components } from "@/lib/api/client";

export type PreferencesIn = components["schemas"]["PreferencesIn"];

/** Stores UI preferences on the user, so they follow them to other devices. True when saved. */
export async function savePreferences(body: PreferencesIn): Promise<boolean> {
  try {
    const { response } = await api.PATCH("/api/account/preferences", {
      body,
    });
    return response.ok;
  } catch {
    return false;
  }
}
