'use client';

import * as React from 'react';
import { createContext, useContext, useState, useCallback, useMemo } from 'react';

export type BackgroundState = 'healthy' | 'warning' | 'critical' | 'neutral' | 'analyzing';

interface BackgroundContextValue {
  /** Current background visual state */
  state: BackgroundState;
  /** Animation intensity (0-1) */
  intensity: number;
  /** Whether pulse animation is active */
  pulseActive: boolean;
  /** Set the background state based on health score */
  setFromHealthScore: (score: number) => void;
  /** Set the background state based on severity */
  setFromSeverity: (severity: 'critical' | 'high' | 'medium' | 'low' | 'info') => void;
  /** Set custom state */
  setState: (state: BackgroundState) => void;
  /** Set intensity level */
  setIntensity: (intensity: number) => void;
  /** Toggle pulse animation */
  setPulseActive: (active: boolean) => void;
  /** Get color for current state */
  getColor: () => string;
}

const BackgroundContext = createContext<BackgroundContextValue | null>(null);

const stateColors: Record<BackgroundState, string> = {
  healthy: 'oklch(0.75 0.15 185)',    // Cyan
  warning: 'oklch(0.68 0.20 45)',     // Orange
  critical: 'oklch(0.58 0.22 25)',    // Red
  neutral: 'oklch(0.60 0.25 295)',    // Purple (primary)
  analyzing: 'oklch(0.70 0.20 295)',  // Light purple
};

interface BackgroundProviderProps {
  children: React.ReactNode;
  /** Initial state */
  initialState?: BackgroundState;
  /** Initial intensity */
  initialIntensity?: number;
}

/**
 * Provider for managing the animated 3D background state.
 * The background responds to data states like health score and severity.
 *
 * @example
 * ```tsx
 * // In your layout
 * <BackgroundProvider>
 *   <WireframeBackground />
 *   {children}
 * </BackgroundProvider>
 *
 * // In a component
 * const { setFromHealthScore } = useBackground();
 * useEffect(() => {
 *   setFromHealthScore(healthScore.score);
 * }, [healthScore]);
 * ```
 */
export function BackgroundProvider({
  children,
  initialState = 'neutral',
  initialIntensity = 0.5,
}: BackgroundProviderProps) {
  const [state, setState] = useState<BackgroundState>(initialState);
  const [intensity, setIntensity] = useState(initialIntensity);
  const [pulseActive, setPulseActive] = useState(false);

  const setFromHealthScore = useCallback((score: number) => {
    if (score >= 80) {
      setState('healthy');
      setIntensity(0.3);
      setPulseActive(false);
    } else if (score >= 60) {
      setState('warning');
      setIntensity(0.5);
      setPulseActive(false);
    } else {
      setState('critical');
      setIntensity(0.7);
      setPulseActive(true);
    }
  }, []);

  const setFromSeverity = useCallback(
    (severity: 'critical' | 'high' | 'medium' | 'low' | 'info') => {
      switch (severity) {
        case 'critical':
          setState('critical');
          setIntensity(0.8);
          setPulseActive(true);
          break;
        case 'high':
          setState('warning');
          setIntensity(0.6);
          setPulseActive(true);
          break;
        case 'medium':
          setState('warning');
          setIntensity(0.4);
          setPulseActive(false);
          break;
        case 'low':
        case 'info':
          setState('healthy');
          setIntensity(0.3);
          setPulseActive(false);
          break;
      }
    },
    []
  );

  const getColor = useCallback(() => stateColors[state], [state]);

  const value = useMemo<BackgroundContextValue>(
    () => ({
      state,
      intensity,
      pulseActive,
      setFromHealthScore,
      setFromSeverity,
      setState,
      setIntensity,
      setPulseActive,
      getColor,
    }),
    [state, intensity, pulseActive, setFromHealthScore, setFromSeverity, getColor]
  );

  return (
    <BackgroundContext.Provider value={value}>
      {children}
    </BackgroundContext.Provider>
  );
}

/**
 * Hook to access and control the background state.
 */
export function useBackground() {
  const context = useContext(BackgroundContext);
  if (!context) {
    throw new Error('useBackground must be used within a BackgroundProvider');
  }
  return context;
}

/**
 * Hook that returns only the background state (read-only).
 * Use this when you only need to react to state changes without modifying.
 */
export function useBackgroundState() {
  const context = useContext(BackgroundContext);
  if (!context) {
    return { state: 'neutral' as BackgroundState, intensity: 0.5, pulseActive: false };
  }
  return {
    state: context.state,
    intensity: context.intensity,
    pulseActive: context.pulseActive,
    color: context.getColor(),
  };
}

export { stateColors };
