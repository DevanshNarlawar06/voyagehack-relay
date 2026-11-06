"use client";

import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Badge, Button, Card, PageHeader } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/format";
import type { ChatReply } from "@/lib/types";

interface Line {
  id: number;
  from: "me" | "relay";
  text: string;
  reply?: ChatReply;
}

const SUGGESTIONS = ["How many leaves do I get in probation?", "What's my onboarding status?", "What is my notice period?"];

export default function ChatPage() {
  const [lines, setLines] = useState<Line[]>([]);
  const [text, setText] = useState("");
  const nextId = useRef(0);
  const end = useRef<HTMLDivElement>(null);
  const add = (line: Omit<Line, "id">) => {
    const id = nextId.current;
    nextId.current += 1;
    setLines((current) => [...current, { ...line, id }]);
  };
  const send = useMutation({
    mutationFn: (message: string) => api.chat(message),
    onSuccess: (reply) => add({ from: "relay", text: reply.reply, reply }),
  });

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [lines, send.isPending]);

  const ask = (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || send.isPending) return;
    add({ from: "me", text: trimmed });
    setText("");
    send.mutate(trimmed);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    ask(text);
  };

  return (
    <>
      <PageHeader title="Ask Relay" description="Policy questions answered from the rules, with sources. Anything else goes to a person." />
      <Card className="flex flex-col">
        <div className="max-h-[60vh] min-h-64 space-y-3 overflow-y-auto p-4" role="log" aria-label="Conversation" aria-live="polite">
          {lines.length === 0 && (
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <Button key={suggestion} size="sm" onClick={() => ask(suggestion)}>
                  {suggestion}
                </Button>
              ))}
            </div>
          )}
          {lines.map((line) => (
            <div key={line.id} className={cn("flex", line.from === "me" && "justify-end")}>
              <div className={cn("max-w-[85%] rounded-lg px-3 py-2 text-sm", line.from === "me" ? "bg-accent text-accent-foreground" : "bg-muted")}>
                <p className="whitespace-pre-wrap">{line.text}</p>
                {line.reply && line.reply.citations.length > 0 && (
                  <ul className="mt-2 space-y-0.5 border-t border-border pt-2 text-xs text-muted-foreground">
                    {line.reply.citations.map((citation) => (
                      <li key={citation.id}>
                        Source: {citation.title}. {citation.source}
                      </li>
                    ))}
                  </ul>
                )}
                {line.reply?.routed_to_hr && (
                  <div className="mt-2">
                    <Badge tone="accent">Sent to a person</Badge>
                  </div>
                )}
              </div>
            </div>
          ))}
          {send.isPending && (
            <p role="status" className="text-sm text-muted-foreground">
              Looking that up…
            </p>
          )}
          {send.isError && (
            <p role="alert" className="text-sm text-danger">
              {errorMessage(send.error)}
            </p>
          )}
          <div ref={end} />
        </div>
        <form onSubmit={submit} className="flex gap-2 border-t border-border p-3">
          <label htmlFor="chat-text" className="sr-only">
            Your message
          </label>
          <input
            id="chat-text"
            value={text}
            maxLength={2000}
            autoComplete="off"
            onChange={(event) => setText(event.target.value)}
            placeholder="Ask about leave, notice, gratuity…"
            className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm"
          />
          <Button type="submit" variant="primary" disabled={!text.trim() || send.isPending}>
            Send
          </Button>
        </form>
      </Card>
    </>
  );
}
