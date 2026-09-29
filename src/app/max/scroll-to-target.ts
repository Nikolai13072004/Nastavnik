export function scrollToTarget(target: HTMLElement | null) {
  if (!target) return;

  window.requestAnimationFrame(() => {
    if (!target.isConnected) return;

    target.focus({ preventScroll: true });
    target.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
      block: "center",
    });
  });
}
