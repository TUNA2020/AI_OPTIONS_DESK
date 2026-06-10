import React, { useEffect, useRef } from "react";
import * as THREE from "three";

// Full-viewport animated background: a slow-drifting particle starfield in the
// theme's gold/blue palette plus a wireframe "market terrain" plane that
// undulates beneath the dashboard. Mouse movement adds gentle camera parallax.
function ThreeBackground() {
  const mountRef = useRef(null);

  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x070b1f, 0.0016);

    const camera = new THREE.PerspectiveCamera(
      60,
      window.innerWidth / window.innerHeight,
      1,
      2000
    );
    camera.position.set(0, 40, 320);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    mount.appendChild(renderer.domElement);

    // --- Particle field (two layers for depth) ---
    const makeParticles = (count, spread, size, color, opacity) => {
      const positions = new Float32Array(count * 3);
      for (let i = 0; i < count; i += 1) {
        positions[i * 3] = (Math.random() - 0.5) * spread;
        positions[i * 3 + 1] = (Math.random() - 0.5) * spread * 0.6;
        positions[i * 3 + 2] = (Math.random() - 0.5) * spread;
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      const material = new THREE.PointsMaterial({
        color,
        size,
        sizeAttenuation: true,
        transparent: true,
        opacity,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      });
      return new THREE.Points(geometry, material);
    };

    const goldDust = makeParticles(700, 900, 2.2, 0xe8c97a, 0.5);
    const blueDust = makeParticles(500, 1100, 2.8, 0x1d4ed8, 0.34);
    const tealDust = makeParticles(260, 700, 1.6, 0xf4e7c3, 0.4);
    scene.add(goldDust, blueDust, tealDust);

    // --- Wireframe terrain plane ---
    const terrainGeo = new THREE.PlaneGeometry(1600, 900, 60, 34);
    const terrainMat = new THREE.MeshBasicMaterial({
      color: 0x24398c,
      wireframe: true,
      transparent: true,
      opacity: 0.12
    });
    const terrain = new THREE.Mesh(terrainGeo, terrainMat);
    terrain.rotation.x = -Math.PI / 2.15;
    terrain.position.y = -120;
    scene.add(terrain);
    const basePositions = terrainGeo.attributes.position.array.slice();

    // --- Glowing accent ring (subtle, far back) ---
    const ringGeo = new THREE.TorusGeometry(140, 0.6, 8, 120);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0xe8c97a,
      transparent: true,
      opacity: 0.15
    });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.position.set(220, 60, -420);
    ring.rotation.x = Math.PI / 3;
    scene.add(ring);

    // --- Mouse parallax ---
    const mouse = { x: 0, y: 0 };
    const onMouseMove = (e) => {
      mouse.x = (e.clientX / window.innerWidth - 0.5) * 2;
      mouse.y = (e.clientY / window.innerHeight - 0.5) * 2;
    };
    window.addEventListener("mousemove", onMouseMove, { passive: true });

    const onResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    };
    window.addEventListener("resize", onResize);

    let rafId = 0;
    let running = true;
    const clock = new THREE.Clock();

    const animate = () => {
      if (!running) return;
      rafId = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();

      goldDust.rotation.y = t * 0.012;
      blueDust.rotation.y = -t * 0.008;
      tealDust.rotation.y = t * 0.016;
      tealDust.rotation.x = Math.sin(t * 0.05) * 0.05;

      ring.rotation.z = t * 0.04;

      // Undulating terrain waves
      const pos = terrainGeo.attributes.position;
      for (let i = 0; i < pos.count; i += 1) {
        const x = basePositions[i * 3];
        const y = basePositions[i * 3 + 1];
        pos.array[i * 3 + 2] =
          Math.sin(x * 0.012 + t * 0.6) * 14 + Math.cos(y * 0.014 + t * 0.4) * 12;
      }
      pos.needsUpdate = true;

      // Smooth camera parallax toward the mouse
      camera.position.x += (mouse.x * 26 - camera.position.x) * 0.025;
      camera.position.y += (40 - mouse.y * 18 - camera.position.y) * 0.025;
      camera.lookAt(0, 0, 0);

      renderer.render(scene, camera);
    };

    if (reduceMotion) {
      // Render one static frame only
      renderer.render(scene, camera);
    } else {
      animate();
    }

    // Pause when the tab is hidden to save battery/GPU
    const onVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(rafId);
      } else if (!reduceMotion && !running) {
        running = true;
        clock.start();
        animate();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      running = false;
      cancelAnimationFrame(rafId);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("resize", onResize);
      [goldDust, blueDust, tealDust].forEach((p) => {
        p.geometry.dispose();
        p.material.dispose();
      });
      terrainGeo.dispose();
      terrainMat.dispose();
      ringGeo.dispose();
      ringMat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, []);

  return <div ref={mountRef} className="three-bg" aria-hidden="true" />;
}

export default ThreeBackground;
