export const PROJECT_SECTIONS = [
  "flags",
  "segments",
  "environments",
  "change-requests",
  "audit",
  "keys",
  "integrations",
] as const;

export type ProjectSection = (typeof PROJECT_SECTIONS)[number];

export type Crumb = { label: string; href: string };

export function projectPath(
  project: string,
  section: ProjectSection = "flags",
): string {
  return `/projects/${encodeURIComponent(project)}/${section}`;
}

export function flagPath(project: string, flag: string): string {
  return `${projectPath(project, "flags")}/${encodeURIComponent(flag)}`;
}

/** The New flag page of a project. */
export function newFlagPath(project: string): string {
  return `${projectPath(project, "flags")}/new`;
}

function projectSegments(pathname: string): string[] | null {
  const [, root, project, ...rest] = pathname.split("/");
  return root === "projects" && project ? [project, ...rest] : null;
}

export function projectFromPath(pathname: string): string | null {
  const segments = projectSegments(pathname);
  return segments ? decodeURIComponent(segments[0]) : null;
}

/** The same section in another project; deeper pages and unknown sections fall back to flags. */
export function switchProjectPath(pathname: string, project: string): string {
  const segments = projectSegments(pathname);
  const section = segments && segments.length === 2 ? segments[1] : "";
  return (PROJECT_SECTIONS as readonly string[]).includes(section)
    ? projectPath(project, section as ProjectSection)
    : projectPath(project);
}

/** Where `/` sends a signed-in user: the last project if it still exists, else the first. */
export function homeTarget(
  projects: string[],
  last: string | null,
): string | null {
  if (projects.length === 0) return null;
  return projectPath(
    last !== null && projects.includes(last) ? last : projects[0],
  );
}

export function projectTrail(project: string): Crumb[] {
  return [
    { label: "Projects", href: "/" },
    { label: project, href: projectPath(project) },
  ];
}
