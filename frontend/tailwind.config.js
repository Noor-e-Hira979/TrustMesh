/**
 * Tailwind configuration — the TrustMesh design tokens.
 *
 * Visual direction is spec §14: enterprise AI infrastructure, NOT a hacker terminal or
 * a crypto dashboard. Dark navy/blue-black base, electric cyan primary, teal for
 * verified, amber for warnings, red for blocked, subtle violet for AI reasoning,
 * off-white text, glassmorphism cards with restrained borders and glows.
 *
 * Every colour is defined here once so the pages never hard-code a hex value.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Base surfaces. `base` is the mandated #0B0F19.
        base: {
          DEFAULT: '#0B0F19',
          900: '#070A11',
          800: '#0E1422',
          700: '#131A2B',
          600: '#1A2236',
          500: '#232C43',
        },
        // Electric cyan — the primary action / accent.
        cyan: {
          DEFAULT: '#22D3EE',
          bright: '#67E8F9',
          dim: '#0E7490',
          glow: 'rgba(34, 211, 238, 0.16)',
        },
        // Teal — verified / trusted states.
        teal: {
          DEFAULT: '#2DD4BF',
          dim: '#0F766E',
          glow: 'rgba(45, 212, 191, 0.16)',
        },
        // Amber — warnings, pending chain state.
        amber: {
          DEFAULT: '#F59E0B',
          dim: '#92400E',
          glow: 'rgba(245, 158, 11, 0.16)',
        },
        // Red — blocked actions and security events.
        red: {
          DEFAULT: '#F43F5E',
          dim: '#9F1239',
          glow: 'rgba(244, 63, 94, 0.18)',
        },
        // Violet — AI reasoning layer only. Used sparingly and deliberately.
        violet: {
          DEFAULT: '#A78BFA',
          dim: '#6D28D9',
          glow: 'rgba(167, 139, 250, 0.16)',
        },
        // Off-white text scale.
        ink: {
          DEFAULT: '#E8EDF7',
          muted: '#94A3B8',
          faint: '#64748B',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: {
        xl: '0.875rem',
        '2xl': '1.125rem',
      },
      boxShadow: {
        glass: '0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 32px -8px rgba(0,0,0,0.6)',
        'glow-cyan': '0 0 0 1px rgba(34,211,238,0.25), 0 0 24px -4px rgba(34,211,238,0.35)',
        'glow-teal': '0 0 0 1px rgba(45,212,191,0.25), 0 0 24px -4px rgba(45,212,191,0.30)',
        'glow-red': '0 0 0 1px rgba(244,63,94,0.30), 0 0 28px -4px rgba(244,63,94,0.40)',
        'glow-amber': '0 0 0 1px rgba(245,158,11,0.28), 0 0 22px -4px rgba(245,158,11,0.32)',
        'glow-violet': '0 0 0 1px rgba(167,139,250,0.25), 0 0 22px -4px rgba(167,139,250,0.30)',
      },
      backgroundImage: {
        'grid-faint':
          'linear-gradient(rgba(148,163,184,0.045) 1px, transparent 1px), ' +
          'linear-gradient(90deg, rgba(148,163,184,0.045) 1px, transparent 1px)',
      },
      backgroundSize: {
        grid: '44px 44px',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(14px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 rgba(34,211,238,0.45)' },
          '70%': { boxShadow: '0 0 0 10px rgba(34,211,238,0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(34,211,238,0)' },
        },
        'flow-dash': {
          to: { strokeDashoffset: '-32' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        // Slow horizontal light sweep across the primary CTA.
        sweep: {
          '0%': { transform: 'translateX(-120%) skewX(-18deg)' },
          '100%': { transform: 'translateX(320%) skewX(-18deg)' },
        },
        // Ambient orbs drift very slowly so the landing page never feels static.
        'orb-drift-a': {
          '0%,100%': { transform: 'translate3d(0,0,0) scale(1)' },
          '50%': { transform: 'translate3d(4%,-5%,0) scale(1.08)' },
        },
        'orb-drift-b': {
          '0%,100%': { transform: 'translate3d(0,0,0) scale(1.04)' },
          '50%': { transform: 'translate3d(-5%,4%,0) scale(1)' },
        },
        // A hairline of light travelling a card's top border on hover.
        'border-trace': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
        // CTA halo breathing while the launch transition plays.
        'halo': {
          '0%,100%': { opacity: '0.35', transform: 'scale(1)' },
          '50%': { opacity: '0.7', transform: 'scale(1.06)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 220ms ease-out both',
        'fade-up': 'fade-up 620ms cubic-bezier(0.16,1,0.3,1) both',
        'pulse-ring': 'pulse-ring 2s ease-out infinite',
        'flow-dash': 'flow-dash 900ms linear infinite',
        shimmer: 'shimmer 1.6s infinite',
        sweep: 'sweep 2.4s ease-in-out infinite',
        'orb-drift-a': 'orb-drift-a 26s ease-in-out infinite',
        'orb-drift-b': 'orb-drift-b 32s ease-in-out infinite',
        'border-trace': 'border-trace 1.4s ease-out infinite',
        halo: 'halo 1.5s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
