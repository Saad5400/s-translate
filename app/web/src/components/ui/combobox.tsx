import * as React from "react";
import { Check, ChevronDown, Plus } from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "./command";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";
import { Button } from "./button";
import { cn } from "@/lib/utils";

export type ComboboxOption = {
  value: string;
  label: string;
  sub?: string;
};

interface ComboboxProps {
  value: string;
  onChange: (v: string) => void;
  options: ComboboxOption[];
  placeholder?: string;
  emptyText?: string;
  /** Allow free-text entry (accept what the user typed as the value). */
  allowCustom?: boolean;
  /** Optional label used as aria-label for the trigger. */
  ariaLabel?: string;
  className?: string;
  id?: string;
  /** Optional renderer for each option's label. */
  renderOption?: (o: ComboboxOption) => React.ReactNode;
  /** LTR content hint (model names, language codes). */
  monoValue?: boolean;
}

export function Combobox({
  value,
  onChange,
  options,
  placeholder = "اختر…",
  emptyText = "لا توجد نتائج",
  allowCustom = false,
  ariaLabel,
  className,
  id,
  renderOption,
  monoValue = false,
}: ComboboxProps) {
  const [open, setOpen] = React.useState(false);
  const [search, setSearch] = React.useState("");

  const selected = options.find((o) => o.value === value);
  const displayLabel = selected?.label ?? value;

  const canAddCustom =
    allowCustom &&
    search.trim().length > 0 &&
    !options.some(
      (o) => o.value.toLowerCase() === search.trim().toLowerCase()
    );

  function commit(next: string) {
    onChange(next);
    setOpen(false);
    setSearch("");
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-label={ariaLabel}
          aria-expanded={open}
          className={cn(
            "w-full justify-between bg-ink-1 font-normal",
            "hover:bg-ink-1 hover:border-line-strong",
            "data-[state=open]:border-accent-line data-[state=open]:ring-2 data-[state=open]:ring-accent-soft",
            className
          )}
        >
          <span
            className={cn(
              "flex-1 min-w-0 truncate text-start",
              monoValue && value && "font-mono text-[14px]",
              !value && "text-paper-3"
            )}
            dir={monoValue ? "ltr" : undefined}
          >
            {value ? displayLabel : placeholder}
          </span>
          <ChevronDown className="h-4 w-4 shrink-0 text-paper-2 opacity-70" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="p-0 w-[var(--radix-popover-trigger-width)]"
        align="start"
      >
        <Command
          shouldFilter={true}
          // cmdk filter: lowercase substring against label + value + sub
          filter={(v, q) => {
            const opt = options.find((o) => o.value === v);
            const hay = `${v} ${opt?.label ?? ""} ${opt?.sub ?? ""}`.toLowerCase();
            return hay.includes(q.toLowerCase()) ? 1 : 0;
          }}
        >
          <CommandInput
            placeholder="ابحث…"
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>
              {canAddCustom ? (
                <button
                  type="button"
                  className="inline-flex items-center gap-2 text-accent hover:underline text-[13px]"
                  onClick={() => commit(search.trim())}
                >
                  <Plus className="h-3.5 w-3.5" />
                  استخدام "
                  <span className="font-mono" dir="ltr">{search.trim()}</span>"
                </button>
              ) : (
                emptyText
              )}
            </CommandEmpty>
            <CommandGroup>
              {options.map((o) => (
                <CommandItem
                  key={o.value}
                  value={o.value}
                  onSelect={() => commit(o.value)}
                  className="justify-between"
                >
                  <span className="flex flex-col min-w-0">
                    <span
                      className={cn(
                        "truncate",
                        monoValue && "font-mono text-[13px]"
                      )}
                      dir={monoValue ? "ltr" : undefined}
                    >
                      {renderOption ? renderOption(o) : o.label}
                    </span>
                    {o.sub && (
                      <span className="text-[11px] text-paper-3 truncate">
                        {o.sub}
                      </span>
                    )}
                  </span>
                  {o.value === value && (
                    <Check className="h-4 w-4 shrink-0 text-accent" />
                  )}
                </CommandItem>
              ))}
              {canAddCustom && (
                <CommandItem
                  value={`__add__${search.trim()}`}
                  onSelect={() => commit(search.trim())}
                  className="text-accent"
                >
                  <Plus className="h-4 w-4" />
                  <span>استخدام نموذج مخصّص</span>
                  <span
                    className="ms-auto font-mono text-[12px] truncate"
                    dir="ltr"
                  >
                    {search.trim()}
                  </span>
                </CommandItem>
              )}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
