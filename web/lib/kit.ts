/** Whether the /dev/kit gallery is served (MEGOOFLAGS_UI_KIT=1). */
export function kitEnabled(v: string | undefined): boolean {
  return v === "1";
}
