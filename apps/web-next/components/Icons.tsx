import type { CSSProperties } from "react";

export type IconName =
  | "office"
  | "team"
  | "activity"
  | "shield"
  | "plug"
  | "folder"
  | "arrow"
  | "spark"
  | "code"
  | "search"
  | "terminal"
  | "pen"
  | "chevron"
  | "refresh"
  | "logout"
  | "menu"
  | "close"
  | "check"
  | "clock"
  | "globe"
  | "lock"
  | "send"
  | "stop";
const paths: Record<IconName, React.ReactNode> = {
  office: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
    </>
  ),
  team: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v2" />
    </>
  ),
  activity: (
    <>
      <path d="M3 12h4l3-8 4 16 3-8h4" />
    </>
  ),
  shield: (
    <>
      <path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z" />
      <path d="m8 12 3 3 5-6" />
    </>
  ),
  plug: (
    <>
      <path d="m7 7 10 10M9 4 6 7a5 5 0 0 0 7 7l3-3M14 5l3-3M19 10l3-3M9 15l-7 7" />
    </>
  ),
  folder: (
    <path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  ),
  arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  spark: (
    <>
      <path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z" />
    </>
  ),
  code: (
    <>
      <path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18" />
    </>
  ),
  search: (
    <>
      <circle cx="10" cy="10" r="6" />
      <path d="m15 15 6 6" />
    </>
  ),
  terminal: (
    <>
      <rect x="2" y="3" width="20" height="18" rx="3" />
      <path d="m6 8 4 4-4 4m7 0h5" />
    </>
  ),
  pen: (
    <>
      <path d="m15 4 5 5M3 21l6-1L21 8a3.5 3.5 0 0 0-5-5L4 15z" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  refresh: (
    <>
      <path d="M20 7a9 9 0 1 0 1 8M20 2v5h-5" />
    </>
  ),
  logout: (
    <>
      <path d="M9 3H4v18h5m0-9h13m-5-5 5 5-5 5" />
    </>
  ),
  menu: <path d="M3 5h18M3 12h18M3 19h18" />,
  close: <path d="m5 5 14 14M5 19 19 5" />,
  check: <path d="m4 12 5 5L20 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <ellipse cx="12" cy="12" rx="4" ry="9" />
      <path d="M3 12h18" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3" />
    </>
  ),
  send: (
    <>
      <path d="m3 3 18 9-18 9 3-9zm3 9h15" />
    </>
  ),
  stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
};
export function Icon({
  name,
  size = 18,
  className = "",
  style,
}: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
export function agentIcon(id: string): IconName {
  return (
    (
      {
        manager: "spark",
        coder: "code",
        researcher: "search",
        operator: "terminal",
        writer: "pen",
      } as Record<string, IconName>
    )[id] ?? "team"
  );
}
