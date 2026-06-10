import React, { useEffect, useRef } from "react";
import gsap from "gsap";

// Tweens between the previous and next value whenever `value` changes, so
// P&L / price figures roll smoothly instead of snapping on each refresh.
function AnimatedNumber({ value, digits = 2, signed = false, className = "" }) {
  const spanRef = useRef(null);
  const tracked = useRef({ val: 0 });

  const format = (n) => {
    if (!Number.isFinite(n)) return (0).toFixed(digits);
    const formatted = n.toFixed(digits);
    if (signed && n > 0) return `+${formatted}`;
    return formatted;
  };

  useEffect(() => {
    const target = Number(value || 0);
    const el = spanRef.current;
    if (!el) return undefined;
    const tween = gsap.to(tracked.current, {
      val: target,
      duration: 0.9,
      ease: "power2.out",
      onUpdate: () => {
        el.textContent = format(tracked.current.val);
      }
    });
    return () => tween.kill();
  }, [value, digits, signed]);

  return (
    <span ref={spanRef} className={className}>
      {format(Number(value || 0))}
    </span>
  );
}

export default AnimatedNumber;
