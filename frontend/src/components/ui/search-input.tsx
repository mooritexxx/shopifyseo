import { Search } from "lucide-react";
import { Input } from "./input";
import { cn } from "../../lib/utils";

export function SearchInput({
  value,
  onChange,
  placeholder,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={cn("relative w-full max-w-xl", className)}>
      <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
      <Input
        className="h-10 rounded-lg border-[#d4ddeb] bg-white pl-11 pr-3 py-2 text-sm placeholder:text-slate-400 focus:border-ocean focus:ring-2 focus:ring-[#dbe8ff]"
        aria-label={placeholder || "Search"}
        placeholder={placeholder || "Search…"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}
