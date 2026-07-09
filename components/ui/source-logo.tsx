import type { ProjectSource } from "@/lib/board-utils";

/**
 * Small square badge showing the work-item source's brand mark
 * (Azure DevOps or Jira). Used on project rows so users can tell at a glance
 * which backend a project comes from.
 */
export function SourceLogo({
  source,
  size = 20,
  className = "",
}: {
  source: ProjectSource;
  size?: number;
  className?: string;
}) {
  const isJira = source === "jira";
  const src = isJira ? "/icons/icon_jira.png" : "/icons/icon_ado_color.png";
  const label = isJira ? "Jira project" : "Azure DevOps project";
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-[5px] bg-white ${className}`}
      style={{ width: size, height: size }}
      title={label}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src || "/placeholder.svg"}
        alt={label}
        width={size - 6}
        height={size - 6}
        className="object-contain"
      />
    </span>
  );
}
