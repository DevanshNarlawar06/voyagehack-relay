"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { storedSession, storeSession } from "@/lib/api";
import type { Session } from "@/lib/types";

interface AuthState {
  /** Undefined until the stored session has been read in the browser. */
  session: Session | null | undefined;
  signIn: (session: Session) => void;
  signOut: () => void;
  hasRole: (...roles: string[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null | undefined>(undefined);

  useEffect(() => {
    setSession(storedSession());
  }, []);

  const signIn = useCallback((next: Session) => {
    storeSession(next);
    setSession(next);
  }, []);

  const signOut = useCallback(() => {
    storeSession(null);
    setSession(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      session,
      signIn,
      signOut,
      hasRole: (...roles) => roles.some((role) => session?.user.roles.includes(role)),
    }),
    [session, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
