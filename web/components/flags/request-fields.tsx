"use client";

import { useId } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** The title (at most 200 characters) and description of a change request. */
export function RequestFields({
  title,
  onTitle,
  description,
  onDescription,
}: {
  title: string;
  onTitle: (title: string) => void;
  description: string;
  onDescription: (description: string) => void;
}) {
  const id = useId();
  return (
    <>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-title`}>Title</Label>
        <Input
          id={`${id}-title`}
          value={title}
          maxLength={200}
          onChange={(e) => onTitle(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-description`}>Description</Label>
        <Textarea
          id={`${id}-description`}
          value={description}
          onChange={(e) => onDescription(e.target.value)}
        />
      </div>
    </>
  );
}
