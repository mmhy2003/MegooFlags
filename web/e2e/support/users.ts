/** The users that global setup creates; each spec signs in through its storageState file. */
export type E2EUser = {
  email: string;
  name: string;
  /** Org role, bound by global setup. ADMIN gets admin through /api/setup. */
  role: string;
  storageState: string;
};

export const PASSWORD = "e2e correct horse battery staple";

export const ADMIN: E2EUser = {
  email: "admin@e2e.test",
  name: "Ada Admin",
  role: "admin",
  storageState: "e2e/.auth/admin.json",
};

export const VIEWER: E2EUser = {
  email: "viewer@e2e.test",
  name: "Vic Viewer",
  role: "viewer",
  storageState: "e2e/.auth/viewer.json",
};

/** For specs that change preferences, so they don't disturb the admin's. */
export const PREFS: E2EUser = {
  email: "prefs@e2e.test",
  name: "Pat Prefs",
  role: "admin",
  storageState: "e2e/.auth/prefs.json",
};

export const USERS = [ADMIN, VIEWER, PREFS];
