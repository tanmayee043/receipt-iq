import type { ReactNode } from "react";

function Icon({ children, size = 16 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
    >
      {children}
    </svg>
  );
}

export const CheckIcon = () => (
  <Icon>
    <path d="m3 8.5 3.2 3.2L13 4.8" />
  </Icon>
);

export const FlagIcon = () => (
  <Icon>
    <path d="M4 14V2.5M4 3h8.5l-2 3 2 3H4" />
  </Icon>
);

export const AlertIcon = () => (
  <Icon>
    <path d="M8 2 1.5 13.5h13L8 2zM8 6.5v3.2M8 11.7v.1" />
  </Icon>
);

export const CrossIcon = () => (
  <Icon>
    <path d="m4 4 8 8M12 4l-8 8" />
  </Icon>
);

export const ClockIcon = () => (
  <Icon>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 4.5V8l2.3 1.5" />
  </Icon>
);

export const PencilIcon = () => (
  <Icon>
    <path d="m10.5 2.5 3 3-8 8-3.5.5.5-3.5 8-8z" />
  </Icon>
);

export const InfoIcon = () => (
  <Icon>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 7.3v3.5M8 5.1v.1" />
  </Icon>
);

export const ArrowIcon = () => (
  <Icon size={14}>
    <path d="M3 8h9.5M9 4.5 12.5 8 9 11.5" />
  </Icon>
);

export const ChevronIcon = ({ open }: { open: boolean }) => (
  <span className={`inline-flex transition-transform duration-150 ${open ? "rotate-90" : ""}`}>
    <Icon>
      <path d="m6 3.5 4.5 4.5L6 12.5" />
    </Icon>
  </span>
);

export const SortIcon = ({ active }: { active: boolean }) => (
  <span className={active ? "text-accent" : "opacity-0 group-hover:opacity-60"}>
    <Icon size={12}>
      <path d="M8 3v10M4 9l4 4 4-4" />
    </Icon>
  </span>
);

export const SunIcon = () => (
  <Icon>
    <circle cx="8" cy="8" r="3" />
    <path d="M8 1.5V3M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" />
  </Icon>
);

export const MoonIcon = () => (
  <Icon>
    <path d="M13.5 9.5a6 6 0 0 1-7-7 6 6 0 1 0 7 7z" />
  </Icon>
);
