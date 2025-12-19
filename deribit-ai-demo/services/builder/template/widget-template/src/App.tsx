import React, { useEffect, useMemo, useState } from "react";
import { market } from "./lib/market";

function getInstrument() {
  const p = new URLSearchParams(location.search);
  return p.get("instrument") || "";
}

export default function App() {
  const instrument = useMemo(() => getInstrument(), []);
  const [last, setLast] = useState<number | null>(null);

  useEffect(() => {
    const ch = `ticker.${instrument}.100ms`;
    const handler = (msg: any) => {
      const data = msg?.params?.data;
      if (data?.last_price) setLast(data.last_price);
    };
    market.subscribe([ch], handler);
    return () => market.unsubscribe([ch], handler);
  }, [instrument]);

  return (
    <div style={{
      fontFamily: "system-ui",
      padding: 16,
      backgroundColor: "#0a0a0a",
      color: "#e5e5e5",
      minHeight: "100vh"
    }}>
      <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>Instrument</div>
      <div style={{ fontSize: 18, fontWeight: 600 }}>{instrument}</div>
      <div style={{ marginTop: 16, fontSize: 12, opacity: 0.7, marginBottom: 4 }}>Last Price</div>
      <div style={{ fontSize: 32, fontWeight: 700 }}>
        {last !== null ? `$${last.toLocaleString()}` : "--"}
      </div>
    </div>
  );
}
