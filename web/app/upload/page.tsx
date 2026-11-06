"use client";

import { useMutation } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { Button, Card } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";

const DOCUMENTS = [
  { category: "pan", label: "PAN card" },
  { category: "aadhaar", label: "Aadhaar card" },
  { category: "bank_proof", label: "Cancelled cheque or bank statement" },
  { category: "photo", label: "Passport-size photo" },
];

// The purposes in definitions/policies/rbac.yaml, in words a joiner can agree to.
const PURPOSES = [
  { id: "onboarding_verification", label: "Verify my identity and eligibility from these documents" },
  { id: "payroll_setup", label: "Set up my salary payments into my bank account" },
  { id: "statutory_registration", label: "Register me for provident fund, as the law requires" },
  { id: "it_provisioning", label: "Provide my accounts, devices and building access" },
];
const MAX_MB = 10;

function UploadForm() {
  const token = useSearchParams().get("token");
  const [files, setFiles] = useState<Record<string, File>>({});
  const [consents, setConsents] = useState<string[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: () =>
      api.uploadDocuments(
        token as string,
        Object.entries(files).map(([category, file]) => ({ category, file })),
        consents,
      ),
  });

  if (!token) {
    return (
      <Card role="alert" className="p-6">
        <h1 className="font-medium">This link is incomplete</h1>
        <p className="mt-1 text-sm text-muted-foreground">Please open the full link from your message, or ask HR for a new one.</p>
      </Card>
    );
  }
  if (upload.isSuccess) {
    return (
      <Card role="status" className="flex flex-col items-center gap-2 p-8 text-center">
        <CheckCircle2 className="size-7 text-good" aria-hidden />
        <h1 className="font-medium">Thank you, we have your documents</h1>
        <p className="text-sm text-muted-foreground">
          {upload.data.documents.length} received. There is nothing else you need to do here, and this link no longer works.
        </p>
      </Card>
    );
  }

  const choose = (category: string, file: File | undefined) => {
    setFileError(null);
    if (file && file.size > MAX_MB * 1024 * 1024) {
      setFileError(`${file.name} is larger than ${MAX_MB} MB.`);
      return;
    }
    setFiles((current) => {
      const next = { ...current };
      if (file) next[category] = file;
      else delete next[category];
      return next;
    });
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    upload.mutate();
  };
  const ready = Object.keys(files).length > 0 && consents.includes("onboarding_verification");

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Your joining documents</h1>
        <p className="mt-1 text-sm text-muted-foreground">PDF, JPG or PNG, up to {MAX_MB} MB each. This link works once and expires after 72 hours.</p>
      </div>
      <Card className="divide-y divide-border">
        {DOCUMENTS.map(({ category, label }) => (
          <div key={category} className="p-4">
            <label htmlFor={`file-${category}`} className="block text-sm font-medium">
              {label}
            </label>
            <input
              id={`file-${category}`}
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
              onChange={(event) => choose(category, event.target.files?.[0])}
              className="mt-2 block w-full text-sm text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-surface file:px-3 file:py-1.5 file:text-sm file:text-foreground"
            />
          </div>
        ))}
      </Card>
      {fileError && (
        <p role="alert" className="text-sm text-danger">
          {fileError}
        </p>
      )}
      <Card className="p-4">
        <fieldset>
          <legend className="text-sm font-medium">What you agree we can use your data for</legend>
          <p className="mt-1 text-xs text-muted-foreground">
            Your documents are stored encrypted on our systems. You can ask to see or correct your data, or ask us to erase it, at
            any time. Verifying your documents is needed for us to continue.
          </p>
          <div className="mt-3 space-y-2">
            {PURPOSES.map(({ id, label }) => (
              <label key={id} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={consents.includes(id)}
                  onChange={(event) =>
                    setConsents((current) =>
                      event.target.checked ? [...current, id] : current.filter((purpose) => purpose !== id),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
      </Card>
      {upload.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorMessage(upload.error)}
        </p>
      )}
      <Button type="submit" variant="primary" className="w-full" disabled={!ready || upload.isPending}>
        {upload.isPending ? "Sending…" : "Send my documents"}
      </Button>
    </form>
  );
}

export default function UploadPage() {
  return (
    <main className="mx-auto max-w-lg p-4 py-8">
      <Suspense fallback={null}>
        <UploadForm />
      </Suspense>
    </main>
  );
}
