import Link from "next/link";
import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  ReactNode,
} from "react";

type ButtonVariant = "primary" | "secondary" | "ghost";

function buttonClassName(
  variant: ButtonVariant,
  className: string | undefined,
) {
  return ["button", `button--${variant}`, className].filter(Boolean).join(" ");
}

export function Button({
  children,
  variant = "primary",
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  variant?: ButtonVariant;
}) {
  return (
    <button
      type={type}
      className={buttonClassName(variant, className)}
      {...props}
    >
      {children}
    </button>
  );
}

export function ButtonLink({
  children,
  variant = "primary",
  className,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  children: ReactNode;
  variant?: ButtonVariant;
}) {
  return (
    <Link
      className={buttonClassName(variant, className)}
      {...props}
      href={props.href}
    >
      {children}
    </Link>
  );
}
