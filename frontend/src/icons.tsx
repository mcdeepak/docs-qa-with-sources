// Small inline icons, so the UI needs no icon font and no network access.
import { ReactNode } from "react";

function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

type Props = { size?: number };

export const SearchIcon = (p: Props) => <Icon {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></Icon>;
export const CheckIcon = (p: Props) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="m8.5 12.5 2.5 2.5 4.5-5" /></Icon>;
export const NoAnswerIcon = (p: Props) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 8v5" /><path d="M12 16.5v.01" /></Icon>;
export const FileIcon = (p: Props) => <Icon {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h4" /></Icon>;
export const UploadIcon = (p: Props) => <Icon {...p}><path d="M12 16V4" /><path d="m7 9 5-5 5 5" /><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" /></Icon>;
export const PinIcon = (p: Props) => <Icon {...p}><path d="M12 21s7-6.1 7-11a7 7 0 0 0-14 0c0 4.9 7 11 7 11z" /><circle cx="12" cy="10" r="2.5" /></Icon>;

export function Logo({ size = 26 }: Props) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="currentColor" />
      <path d="M9 10.5h14M9 16h14M9 21.5h8" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="22.5" cy="21.5" r="2" fill="#fff" />
    </svg>
  );
}
