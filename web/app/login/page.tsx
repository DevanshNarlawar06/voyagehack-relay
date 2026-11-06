"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Badge, Button, Card, EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { humanize } from "@/lib/format";

export default function LoginPage() {
  const { session, signIn } = useAuth();
  const router = useRouter();
  const users = useQuery({ queryKey: ["demo-users"], queryFn: () => api.demoUsers() });
  const login = useMutation({
    mutationFn: (email: string) => api.login(email),
    onSuccess: signIn,
  });

  useEffect(() => {
    if (session) router.replace("/");
  }, [session, router]);

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center p-4">
      <h1 className="text-xl font-semibold tracking-tight">Sign in to Relay</h1>
      <p className="mt-1 mb-6 text-sm text-muted-foreground">
        Demo sign-in: choose a seeded user. Each one sees what their role allows.
      </p>
      {users.isPending && <LoadingState label="Loading demo users" rows={4} />}
      {users.isError && <ErrorState error={users.error} onRetry={() => users.refetch()} />}
      {users.data?.length === 0 && (
        <EmptyState title="No demo users" description="Run the seed loader to create them." />
      )}
      {users.data && users.data.length > 0 && (
        <Card className="divide-y divide-border">
          {users.data.map((user) => (
            <div key={user.email} className="flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{user.display_name}</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {user.roles.map((role) => (
                    <Badge key={role} tone="accent">
                      {humanize(role)}
                    </Badge>
                  ))}
                </div>
              </div>
              <Button
                size="sm"
                disabled={login.isPending}
                aria-label={`Sign in as ${user.display_name}`}
                onClick={() => login.mutate(user.email)}
              >
                Sign in
              </Button>
            </div>
          ))}
        </Card>
      )}
      {login.isError && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {errorMessage(login.error)}
        </p>
      )}
    </main>
  );
}
