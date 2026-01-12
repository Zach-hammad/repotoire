'use client';

import { useState, useEffect, useCallback } from 'react';

// Visual effects settings stored in localStorage
const VISUAL_SETTINGS_KEY = 'repotoire-visual-settings';

export interface VisualSettings {
  enable3D: boolean;
  enableGlow: boolean;
  enableAnimatedBackground: boolean;
}

const defaultVisualSettings: VisualSettings = {
  enable3D: true,
  enableGlow: true,
  enableAnimatedBackground: true,
};

function getVisualSettings(): VisualSettings {
  if (typeof window === 'undefined') return defaultVisualSettings;
  try {
    const stored = localStorage.getItem(VISUAL_SETTINGS_KEY);
    return stored ? { ...defaultVisualSettings, ...JSON.parse(stored) } : defaultVisualSettings;
  } catch {
    return defaultVisualSettings;
  }
}

/**
 * Hook to access and update visual settings from any component.
 * Settings are stored in localStorage and broadcast via custom events.
 */
export function useVisualSettings() {
  const [settings, setSettingsState] = useState<VisualSettings>(defaultVisualSettings);
  const [isLoaded, setIsLoaded] = useState(false);

  // Load settings on mount
  useEffect(() => {
    setSettingsState(getVisualSettings());
    setIsLoaded(true);
  }, []);

  // Listen for changes from other components
  useEffect(() => {
    const handleChange = (event: CustomEvent<VisualSettings>) => {
      setSettingsState(event.detail);
    };

    window.addEventListener('visual-settings-changed', handleChange as EventListener);
    return () => {
      window.removeEventListener('visual-settings-changed', handleChange as EventListener);
    };
  }, []);

  const updateSettings = useCallback((newSettings: Partial<VisualSettings>) => {
    const updated = { ...settings, ...newSettings };
    setSettingsState(updated);
    localStorage.setItem(VISUAL_SETTINGS_KEY, JSON.stringify(updated));
    window.dispatchEvent(new CustomEvent('visual-settings-changed', { detail: updated }));
  }, [settings]);

  return {
    settings,
    isLoaded,
    updateSettings,
    // Convenience properties
    is3DEnabled: settings.enable3D,
    isGlowEnabled: settings.enableGlow,
    isAnimatedBackgroundEnabled: settings.enableAnimatedBackground,
  };
}

/**
 * Static function to set visual settings (for use outside React components)
 */
export function setVisualSettings(settings: Partial<VisualSettings>): void {
  if (typeof window === 'undefined') return;
  const current = getVisualSettings();
  const updated = { ...current, ...settings };
  localStorage.setItem(VISUAL_SETTINGS_KEY, JSON.stringify(updated));
  window.dispatchEvent(new CustomEvent('visual-settings-changed', { detail: updated }));
}

export { getVisualSettings };
