"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";

type Comment = components["schemas"]["CommentOut"];

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** A change request's comments, oldest first, and a comment box when `canComment`. */
export function Comments({
  project,
  id,
  comments,
  canComment,
}: {
  project: string;
  id: string;
  comments: Comment[];
  canComment: boolean;
}) {
  const fieldId = useId();
  const headingId = useId();
  const [added, setAdded] = useState<Comment[]>([]);
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  // Comments added here stay until the request's own list has them.
  const shown = [
    ...comments,
    ...added.filter((a) => !comments.some((c) => c.id === a.id)),
  ];

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    try {
      const comment = await must(
        api.POST("/api/projects/{project}/change-requests/{id}/comments", {
          params: { path: { project, id } },
          body: { body: body.trim() },
        }),
      );
      setAdded((list) => [...list, comment]);
      setBody("");
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("comment failed", error);
      toast.error(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
    } finally {
      setSending(false);
    }
  };

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h2 id={headingId} className="text-lg font-semibold">
        Comments
      </h2>
      {shown.length === 0 ? (
        <p className="text-muted-foreground">No comments yet.</p>
      ) : (
        <ul aria-label="Comments" className="flex flex-col gap-3">
          {shown.map((c) => (
            <li
              key={c.id}
              className="flex min-w-0 flex-col gap-1 rounded-lg border p-3"
            >
              <div className="flex flex-wrap gap-x-2 text-sm text-muted-foreground">
                <span className="break-all">{c.author}</span>
                <time dateTime={c.created_at}>
                  {DATE_TIME.format(new Date(c.created_at))}
                </time>
              </div>
              <p className="break-words whitespace-pre-wrap">{c.body}</p>
            </li>
          ))}
        </ul>
      )}
      {canComment && (
        <form onSubmit={submit} className="flex flex-col gap-2">
          <Label htmlFor={fieldId}>Comment</Label>
          <Textarea
            id={fieldId}
            value={body}
            maxLength={10000}
            onChange={(e) => setBody(e.target.value)}
          />
          <div>
            <Button
              type="submit"
              className="min-h-11"
              disabled={sending || body.trim() === ""}
            >
              Comment
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
