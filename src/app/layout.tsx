import type { Metadata } from "next";
import Script from "next/script";
import localFont from "next/font/local";
import { AppShell } from "@/components/AppShell";
import { Providers } from "@/components/Providers";
import { ScrollToTopButton } from "@/components/ScrollToTopButton";
import { getPlatformBranding } from "@/lib/platform-settings";
import "./globals.css";

// Дизайн-язык Trenning: IBM Plex Sans (интерфейс), Onest (заголовки),
// IBM Plex Mono (числа/коды). Все три с кириллицей.
const ibmSans = localFont({
  src: "./fonts/IBMPlexSans-Variable.woff2",
  variable: "--font-ibm-sans",
  weight: "400 600",
  display: "swap",
});

const ibmMono = localFont({
  src: [
    { path: "./fonts/IBMPlexMono-Medium.woff2", weight: "500", style: "normal" },
    { path: "./fonts/IBMPlexMono-SemiBold.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-ibm-mono",
  display: "swap",
});

const onest = localFont({
  src: "./fonts/Onest-Variable.woff2",
  variable: "--font-onest",
  weight: "500 700",
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const branding = await getPlatformBranding();

  return {
    title: branding.siteName,
    description: branding.siteDescription,
    icons: branding.faviconUrl
      ? {
          icon: branding.faviconUrl,
          shortcut: branding.faviconUrl,
          apple: branding.faviconUrl,
        }
      : undefined,
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ru"
      suppressHydrationWarning
      className={`${ibmSans.variable} ${ibmMono.variable} ${onest.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-[var(--canvas)] text-[var(--ink)]">
        <Script id="theme-init" strategy="beforeInteractive">
          {"(function(){try{var t=localStorage.getItem('theme');if(t==='dark'||t==='light'){document.documentElement.setAttribute('data-theme',t);}else if(window.matchMedia('(prefers-color-scheme: dark)').matches){document.documentElement.setAttribute('data-theme','dark');}}catch(e){}})();"}
        </Script>
        <Providers>
          <AppShell>{children}</AppShell>
          <ScrollToTopButton />
        </Providers>
      </body>
    </html>
  );
}
