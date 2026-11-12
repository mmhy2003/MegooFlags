import { cookies, headers } from "next/headers";

import { HttpWarning } from "@/components/shell/http-warning";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { shouldWarnPlainHttp } from "@/lib/http-warning";
import { parseTheme, THEME_COOKIE } from "@/lib/theme";

/** Theme (seeded from the cookie so nothing flashes), the plain-HTTP banner and toasts. */
export async function RootProviders({
  children,
}: {
  children: React.ReactNode;
}) {
  const c = await cookies();
  const h = await headers();
  return (
    <ThemeProvider
      attribute="class"
      enableSystem
      defaultTheme={parseTheme(c.get(THEME_COOKIE)?.value) ?? "system"}
      storageKey={THEME_COOKIE}
      disableTransitionOnChange
    >
      <HttpWarning
        show={shouldWarnPlainHttp({
          forwardedProto: h.get("x-forwarded-proto"),
          host: h.get("host"),
        })}
      />
      {children}
      <Toaster />
    </ThemeProvider>
  );
}
