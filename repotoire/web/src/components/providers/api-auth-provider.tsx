"use client";

import { useAuth } from "@clerk/nextjs";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { setAuthTokenGetter } from "@/lib/api";

interface ApiAuthContextType {
  isAuthReady: boolean;
}

const ApiAuthContext = createContext<ApiAuthContextType>({ isAuthReady: false });

export function useApiAuth() {
  return useContext(ApiAuthContext);
}

interface ApiAuthProviderProps {
  children: ReactNode;
}

/**
 * Provider that connects Clerk authentication to the API client.
 * Must be rendered inside ClerkProvider.
 *
 * Exposes `isAuthReady` which is true once Clerk has loaded and
 * the token getter has been set up. Use this to delay API calls
 * until auth is ready.
 */
export function ApiAuthProvider({ children }: ApiAuthProviderProps) {
  const { getToken, isLoaded } = useAuth();
  const [isAuthReady, setIsAuthReady] = useState(false);

  useEffect(() => {
    if (!isLoaded) return;

    // Set up the token getter for API requests
    setAuthTokenGetter(async () => {
      try {
        return await getToken();
      } catch {
        return null;
      }
    });

    // Mark auth as ready once Clerk is loaded and token getter is set
    setIsAuthReady(true);
  }, [getToken, isLoaded]);

  return (
    <ApiAuthContext.Provider value={{ isAuthReady }}>
      {children}
    </ApiAuthContext.Provider>
  );
}
