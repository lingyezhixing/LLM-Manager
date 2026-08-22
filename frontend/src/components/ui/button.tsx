import { type ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "default" | "ghost" | "destructive" | "outline";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  default: "bg-primary text-primary-foreground hover:bg-primary-600",
  ghost: "bg-transparent text-muted-foreground hover:bg-card-hover hover:text-foreground",
  destructive: "bg-destructive text-destructive-foreground hover:bg-destructive-600",
  outline: "border border-border bg-card text-foreground hover:bg-card-hover",
};
const SIZES: Record<Size, string> = { sm: "h-8 px-3 text-xs", md: "h-9 px-4 text-sm" };

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant = "default", size = "md", ...props }, ref) => (
    <button
      ref={ref}
      className={`inline-flex items-center justify-center rounded-md font-medium transition-[color,background-color,border-color,opacity,transform,box-shadow] duration-(--motion-fast) active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:saturate-0 disabled:active:scale-100 ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...props}
    />
  ),
);
Button.displayName = "Button";
