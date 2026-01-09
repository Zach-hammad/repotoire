'use client';

import { createContext, useContext, useState, useCallback, ReactNode, useEffect } from 'react';
import { useRepositories } from '@/lib/hooks';
import { RepositoryInfo } from '@/lib/api';

interface RepositoryContextValue {
  /** Currently selected repository (null = all repositories) */
  selectedRepository: RepositoryInfo | null;
  /** ID of the selected repository (null = all) */
  selectedRepositoryId: string | null;
  /** Set the selected repository by ID */
  setSelectedRepositoryId: (id: string | null) => void;
  /** All available repositories */
  repositories: RepositoryInfo[];
  /** Whether repositories are loading */
  isLoading: boolean;
}

const RepositoryContext = createContext<RepositoryContextValue | null>(null);

export function RepositoryProvider({ children }: { children: ReactNode }) {
  const { data: repositories, isLoading } = useRepositories();
  const [selectedRepositoryId, setSelectedRepositoryId] = useState<string | null>(null);

  // Persist selection in sessionStorage
  useEffect(() => {
    const stored = sessionStorage.getItem('selectedRepositoryId');
    if (stored && stored !== 'null') {
      setSelectedRepositoryId(stored);
    }
  }, []);

  const handleSetSelectedRepositoryId = useCallback((id: string | null) => {
    setSelectedRepositoryId(id);
    if (id) {
      sessionStorage.setItem('selectedRepositoryId', id);
    } else {
      sessionStorage.removeItem('selectedRepositoryId');
    }
  }, []);

  const selectedRepository = repositories?.find(r => r.id === selectedRepositoryId) ?? null;

  return (
    <RepositoryContext.Provider
      value={{
        selectedRepository,
        selectedRepositoryId,
        setSelectedRepositoryId: handleSetSelectedRepositoryId,
        repositories: repositories ?? [],
        isLoading,
      }}
    >
      {children}
    </RepositoryContext.Provider>
  );
}

export function useRepositoryContext() {
  const context = useContext(RepositoryContext);
  if (!context) {
    throw new Error('useRepositoryContext must be used within a RepositoryProvider');
  }
  return context;
}
