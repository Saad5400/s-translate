import * as React from "react";
import { Eye, EyeOff } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "onChange"> {
  value: string;
  onChange: (v: string) => void;
}

export function SecretField({ value, onChange, className, placeholder, ...rest }: Props) {
  const [shown, setShown] = React.useState(false);
  return (
    <div
      className={cn(
        "flex items-stretch rounded-md border border-line bg-ink-1 overflow-hidden",
        "focus-within:border-accent-line focus-within:ring-2 focus-within:ring-accent-soft",
        className
      )}
    >
      <input
        type={shown ? "text" : "password"}
        dir="ltr"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="flex-1 h-11 px-4 bg-transparent outline-none text-paper-0 placeholder:text-paper-3 font-mono text-[13px]"
        {...rest}
      />
      <button
        type="button"
        onClick={() => setShown((s) => !s)}
        aria-label={shown ? "إخفاء" : "إظهار"}
        className="px-3 flex items-center justify-center text-paper-2 hover:text-paper-0 hover:bg-white/5 border-s border-line transition-colors"
      >
        {shown ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}
