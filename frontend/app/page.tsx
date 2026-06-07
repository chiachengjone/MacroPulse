"use client";

import dynamic from "next/dynamic";

// Disable SSR — the app makes live SSE calls to Cloud Run and has no
// server-renderable content; client-only rendering is correct here.
const Dashboard = dynamic(() => import("./_dashboard"), {
  ssr: false,
  loading: () => <div className="min-h-screen bg-[#0a0a0a]" />,
});

export default function Page() {
  return <Dashboard />;
}