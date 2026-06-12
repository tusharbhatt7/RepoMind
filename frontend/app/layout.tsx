import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

const jakartaSans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-jakarta",
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "RepoMind",
  description: "RepoMind — your developer documentation agent",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`dark h-full ${jakartaSans.variable}`}>
      <body className="flex h-full overflow-hidden bg-background text-foreground antialiased font-[family-name:var(--font-jakarta)]">
        <Sidebar />
        <main className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {children}
        </main>
      </body>
    </html>
  );
}
