import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Arivu — 1092 Helpline Console",
  description: "Understand first. Respond right.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Fraunces:wght@400;600;700&family=Instrument+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;600&family=Noto+Sans+Kannada:wght@400;600&display=swap"
        />
      </head>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
