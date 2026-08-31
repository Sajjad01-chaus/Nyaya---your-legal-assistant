import type { Metadata } from "next";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Nyaya - BNSS assistant",
  description:
    "Grounded question answering over the Bharatiya Nagarik Suraksha Sanhita, 2023.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <Nav />
          {children}
          <div style={{
            marginTop: 48,
            padding: "16px 12px",
            borderTop: "1px solid var(--border)",
            fontSize: 11,
            color: "var(--muted)",
            textAlign: "center"
          }}>
            <strong>Disclaimer:</strong> Nyaya is an AI assistant grounded in the Bharatiya Nagarik Suraksha Sanhita, 2023.
            It is not a substitute for professional legal advice. Always consult a qualified lawyer for your specific situation.
          </div>
        </div>
      </body>
    </html>
  );
}
