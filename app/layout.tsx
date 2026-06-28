import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AgentProvider } from "@/lib/agent-context";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Testing Toolkit",
  description:
    "Unified Azure DevOps toolkit: test case generation, bulk defect upload, and work-item PDF packaging.",
};

export const viewport = {
  themeColor: "#0d1017",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
      style={{ background: "#0d1017" }}
    >
      <body className="h-full overflow-hidden bg-background text-foreground">
        <AgentProvider>{children}</AgentProvider>
      </body>
    </html>
  );
}
