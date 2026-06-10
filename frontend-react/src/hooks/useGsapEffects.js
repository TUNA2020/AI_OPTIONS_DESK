import { useEffect } from "react";
import gsap from "gsap";

// Staggered entrance for dashboard panels. Re-runs when `deps` change
// (e.g. switching pages) so newly mounted panels animate in too.
export function usePanelEntrance(deps = []) {
  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const panels = gsap.utils.toArray(".gsap-panel");
    if (!panels.length) return undefined;
    if (reduceMotion) {
      gsap.set(panels, { opacity: 1, y: 0 });
      return undefined;
    }
    const tl = gsap.fromTo(
      panels,
      { opacity: 0, y: 28, scale: 0.985 },
      {
        opacity: 1,
        y: 0,
        scale: 1,
        duration: 0.7,
        ease: "power3.out",
        stagger: 0.08,
        clearProps: "transform"
      }
    );
    return () => tl.kill();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

// Magnetic pull on buttons: the button leans toward the cursor and springs
// back on leave. Attached via event delegation so dynamically rendered
// buttons work without re-binding.
export function useMagneticButtons() {
  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) return undefined;

    const strength = 0.35;

    const onMove = (e) => {
      const btn = e.target.closest?.(".magnetic-btn");
      if (!btn) return;
      const rect = btn.getBoundingClientRect();
      const x = e.clientX - rect.left - rect.width / 2;
      const y = e.clientY - rect.top - rect.height / 2;
      gsap.to(btn, {
        x: x * strength,
        y: y * strength,
        duration: 0.3,
        ease: "power2.out"
      });
    };

    const onLeave = (e) => {
      const btn = e.target.closest?.(".magnetic-btn");
      if (!btn) return;
      gsap.to(btn, {
        x: 0,
        y: 0,
        duration: 0.6,
        ease: "elastic.out(1, 0.4)"
      });
    };

    document.addEventListener("mousemove", onMove, { passive: true });
    document.addEventListener("mouseout", onLeave, { passive: true });
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseout", onLeave);
    };
  }, []);
}

// One-shot hero header reveal: title slides up, subtitle and controls fade in.
export function useHeroReveal() {
  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      gsap.set([".hero-title", ".hero-subtitle", ".hero-controls", ".page-nav"], {
        opacity: 1,
        y: 0
      });
      return undefined;
    }
    const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
    tl.fromTo(".hero-title", { opacity: 0, y: 24 }, { opacity: 1, y: 0, duration: 0.8 })
      .fromTo(
        ".hero-subtitle",
        { opacity: 0, y: 14 },
        { opacity: 1, y: 0, duration: 0.6 },
        "-=0.5"
      )
      .fromTo(
        ".hero-controls",
        { opacity: 0, x: 18 },
        { opacity: 1, x: 0, duration: 0.6 },
        "-=0.4"
      )
      .fromTo(
        ".page-nav",
        { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.5 },
        "-=0.35"
      );
    return () => tl.kill();
  }, []);
}

// Pulse flash on a P&L element when its value changes direction/magnitude.
export function flashPnl(el, positive) {
  if (!el) return;
  gsap.fromTo(
    el,
    { textShadow: `0 0 18px ${positive ? "rgba(34,197,94,0.9)" : "rgba(239,68,68,0.9)"}` },
    { textShadow: "0 0 0px rgba(0,0,0,0)", duration: 1.2, ease: "power2.out" }
  );
}
