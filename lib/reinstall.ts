import { setPendingReinstallPref } from "./preferences";

/**
 * Enter the fresh-installer flow without resetting user state.
 *
 * The installer refreshes the local agent binaries and transient distribution
 * cache. Existing connection settings, credentials, generated artifacts, UI
 * preferences, and selected project/board are preserved.
 * Normal KB currency checks decide whether any index needs rebuilding.
 */
export function requestReinstall() {
  setPendingReinstallPref(true);
  if (typeof window !== "undefined") window.location.reload();
}
