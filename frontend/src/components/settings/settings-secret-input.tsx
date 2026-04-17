import { Eye, EyeOff } from "lucide-react";
import { useState, type ChangeEvent } from "react";

import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { cn } from "../../lib/utils";

type SecretInputProps = {
  id: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
  placeholder?: string;
  ariaDescribedBy?: string;
  className?: string;
};

export function SettingsSecretInput({ id, value, onChange, placeholder, ariaDescribedBy, className }: SecretInputProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? "text" : "password"}
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        aria-describedby={ariaDescribedBy}
        className={cn("rounded-2xl border border-line bg-white px-4 py-3 pr-12 outline-none", className)}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="absolute right-1 top-1/2 h-9 w-9 -translate-y-1/2 shrink-0 text-slate-500 hover:bg-slate-100 hover:text-ink"
        aria-label={visible ? "Hide value" : "Show value"}
        aria-controls={id}
        aria-pressed={visible}
        onClick={() => setVisible((v) => !v)}
      >
        {visible ? <EyeOff size={18} strokeWidth={2} aria-hidden /> : <Eye size={18} strokeWidth={2} aria-hidden />}
      </Button>
    </div>
  );
}
