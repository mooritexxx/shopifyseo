import type { ReactNode } from "react";

import { cn } from "../../lib/utils";
import { Switch } from "./switch";

export type ToggleSwitchProps = {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
  id?: string;
  className?: string;
};

export function ToggleSwitch({ checked, onCheckedChange, label, disabled, id, className }: ToggleSwitchProps) {
  const switchId = id ?? "toggle-switch";

  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 rounded-2xl border px-3 py-2 text-sm transition",
        checked ? "border-[#ffbe82]/70 bg-[#7b3d14]/40 text-white" : "border-white/10 bg-white/5 text-white/80",
        disabled ? "opacity-60" : "",
        className
      )}
    >
      <label htmlFor={switchId} className="select-none text-white">
        {label}
      </label>
      <Switch
        id={switchId}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className={cn(
          "h-7 w-12 data-[state=checked]:bg-[#ffbe82] data-[state=unchecked]:bg-white/25",
          "focus-visible:ring-[#ffbe82]/80 focus-visible:ring-offset-[#0d172b]"
        )}
      />
    </div>
  );
}
