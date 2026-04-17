import { CircleHelp } from "lucide-react";
import type { ReactNode } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { settingsFieldHintId } from "./settings-field-meta";

export type SettingsFieldShellProps = {
  fieldId: string;
  label: string;
  hint: string;
  detail?: string;
  children: ReactNode;
};

/**
 * Label row (with optional info popover), control slot, then hint with stable id for aria-describedby.
 * Pass id={fieldId} and aria-describedby={settingsFieldHintId(fieldId)} on inputs/select triggers when applicable.
 */
export function SettingsFieldShell({ fieldId, label, hint, detail, children }: SettingsFieldShellProps) {
  const hintId = settingsFieldHintId(fieldId);
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <label htmlFor={fieldId} className="text-sm font-medium text-ink">
          {label}
        </label>
        {detail ? (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 transition hover:bg-[#f0f4fa] hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ocean focus-visible:ring-offset-2"
                aria-label={`More about ${label}`}
              >
                <CircleHelp className="size-4" aria-hidden />
              </button>
            </PopoverTrigger>
            <PopoverContent className="leading-relaxed text-slate-600" align="start">
              <p className="whitespace-pre-wrap">{detail}</p>
            </PopoverContent>
          </Popover>
        ) : null}
      </div>
      {children}
      {hint ? (
        <p id={hintId} className="text-xs font-normal text-slate-400">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
