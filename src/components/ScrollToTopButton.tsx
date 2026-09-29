"use client";

import { useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";
import { usePathname } from "next/navigation";

const SCROLL_OFFSET = 240;

export function ScrollToTopButton() {
  const [visible, setVisible] = useState(false);
  const isMaxPage = usePathname() === "/max";

  useEffect(() => {
    const updateVisibility = () => {
      const root = document.documentElement;
      const canScroll = root.scrollHeight > window.innerHeight + 8;
      setVisible(canScroll && window.scrollY > SCROLL_OFFSET);
    };

    updateVisibility();

    window.addEventListener("scroll", updateVisibility, { passive: true });
    window.addEventListener("resize", updateVisibility);

    const resizeObserver = new ResizeObserver(updateVisibility);
    resizeObserver.observe(document.body);

    return () => {
      window.removeEventListener("scroll", updateVisibility);
      window.removeEventListener("resize", updateVisibility);
      resizeObserver.disconnect();
    };
  }, []);

  return (
    <button
      type="button"
      aria-label="Наверх"
      title="Наверх"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      className={`fixed bottom-5 right-5 z-50 inline-flex h-12 w-12 items-center justify-center rounded-full transition duration-200 focus:outline-none focus-visible:ring-4 md:bottom-6 md:right-6 ${
        isMaxPage
          ? "bg-[#087cf0] text-[#fff] shadow-lg ring-1 ring-white/20 hover:bg-[#006bd6] focus-visible:ring-[#8cc9ff]"
          : "bg-[var(--accent)] text-white shadow-[var(--shadow-2)] ring-1 ring-[var(--line)] hover:bg-[var(--accent-strong)] focus-visible:ring-[var(--accent-soft)]"
      } ${
        visible ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-3 opacity-0"
      }`}
    >
      <ArrowUp className="h-6 w-6" aria-hidden="true" strokeWidth={2.4} />
    </button>
  );
}
