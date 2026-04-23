import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-line disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-ink-2 text-paper-0 border border-line hover:bg-ink-3 hover:border-line-strong",
        primary:
          "bg-paper-0 text-ink-0 font-semibold hover:bg-white border border-paper-0",
        accent:
          "bg-accent text-accent-ink font-semibold hover:brightness-105 border border-accent",
        ghost:
          "bg-transparent text-paper-0 hover:bg-white/5 border border-transparent",
        outline:
          "bg-transparent text-paper-0 border border-line hover:bg-white/5 hover:border-line-strong",
        destructive:
          "bg-danger text-white hover:brightness-105 border border-danger",
      },
      size: {
        default: "h-11 px-5 text-sm",
        sm: "h-8 px-3 text-[13px] rounded-sm",
        lg: "h-12 px-6 text-base",
        icon: "h-11 w-11 p-0",
        iconSm: "h-8 w-8 p-0 rounded-sm",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { buttonVariants };
