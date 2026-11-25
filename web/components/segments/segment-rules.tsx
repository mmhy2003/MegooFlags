"use client";

import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useRef } from "react";

import { ClauseRow } from "@/components/flags/clause-row";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  addClause,
  addRule,
  moveRule,
  removeClause,
  removeRule,
  type SegmentDraft,
  type SegmentRuleDraft,
  updateClause,
  updateRule,
} from "@/lib/segments/segment-draft";

type MoveButtons = Map<string, HTMLButtonElement | null>;

/** One rule of a segment: a description, its clauses (all must match), and move and remove. */
function RuleCard({
  rule,
  index,
  count,
  readOnly,
  problems,
  moveButtons,
  onChange,
  onMove,
  onRemove,
}: {
  rule: SegmentRuleDraft;
  index: number;
  count: number;
  readOnly: boolean;
  problems: Record<string, string>;
  /** The move buttons by `<rule id>:up|down`, so focus can follow a moved rule. */
  moveButtons: React.RefObject<MoveButtons>;
  onChange: (edit: (d: SegmentDraft) => SegmentDraft) => void;
  onMove: (by: -1 | 1) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const n = index + 1;
  const path = `rules[${index}]`;
  const button = (dir: "up" | "down") => (el: HTMLButtonElement | null) => {
    moveButtons.current.set(`${rule.id}:${dir}`, el);
  };

  return (
    <Card role="group" aria-labelledby={`${id}-title`} className="min-w-0 px-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`${id}-title`} className="font-semibold">
          Rule {n}
        </h3>
        {!readOnly && (
          <div className="flex gap-2">
            <Button
              ref={button("up")}
              variant="outline"
              size="icon"
              className="min-h-11 min-w-11"
              aria-label={`Move rule ${n} up`}
              disabled={index === 0}
              onClick={() => onMove(-1)}
            >
              <ArrowUp aria-hidden />
            </Button>
            <Button
              ref={button("down")}
              variant="outline"
              size="icon"
              className="min-h-11 min-w-11"
              aria-label={`Move rule ${n} down`}
              disabled={index === count - 1}
              onClick={() => onMove(1)}
            >
              <ArrowDown aria-hidden />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="min-h-11 min-w-11"
              aria-label={`Remove rule ${n}`}
              onClick={onRemove}
            >
              <Trash2 aria-hidden />
            </Button>
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-description`}>Description</Label>
        <Input
          id={`${id}-description`}
          className="h-11 text-base"
          value={rule.description}
          readOnly={readOnly}
          onChange={(e) =>
            onChange((d) =>
              updateRule(d, rule.id, { description: e.target.value }),
            )
          }
        />
      </div>
      <ol className="flex min-w-0 flex-col gap-3">
        {rule.clauses.map((clause, j) => (
          <li key={clause.id} className="flex min-w-0 flex-col gap-3">
            {j > 0 && (
              <span className="text-sm font-medium text-muted-foreground uppercase">
                and
              </span>
            )}
            <ClauseRow
              clause={clause}
              allowSegments={false}
              readOnly={readOnly}
              canRemove={rule.clauses.length > 1}
              error={problems[`${path}.clauses[${j}]`]}
              onChange={(patch) =>
                onChange((d) => updateClause(d, rule.id, clause.id, patch))
              }
              onRemove={() =>
                onChange((d) => removeClause(d, rule.id, clause.id))
              }
            />
          </li>
        ))}
      </ol>
      {!readOnly && (
        <Button
          variant="outline"
          className="min-h-11 w-fit"
          onClick={() => onChange((d) => addClause(d, rule.id))}
        >
          <Plus aria-hidden />
          Add clause
        </Button>
      )}
      {problems[path] && (
        <p className="text-sm text-destructive">{problems[path]}</p>
      )}
    </Card>
  );
}

/**
 * A segment's rules: a context matching every clause of any rule is in the segment. Every edit
 * hands a new draft to `onChange`.
 */
export function SegmentRules({
  draft,
  onChange,
  readOnly,
  problems,
}: {
  draft: SegmentDraft;
  onChange: (draft: SegmentDraft) => void;
  readOnly: boolean;
  /** Problems by field (see `problemField`). */
  problems: Record<string, string>;
}) {
  const moveButtons = useRef<MoveButtons>(new Map());
  // The move button to focus once a moved rule has re-rendered in its new place.
  const focusAfterMove = useRef<{ ruleId: string; dir: "up" | "down" } | null>(
    null,
  );
  useEffect(() => {
    const pending = focusAfterMove.current;
    if (!pending) return;
    focusAfterMove.current = null;
    const other = pending.dir === "up" ? "down" : "up";
    const same = moveButtons.current.get(`${pending.ruleId}:${pending.dir}`);
    // At an end the pressed direction is disabled, so focus the other one.
    const target =
      same && !same.disabled
        ? same
        : moveButtons.current.get(`${pending.ruleId}:${other}`);
    target?.focus();
  }, [draft.rules]);

  return (
    <>
      {draft.rules.length === 0 ? (
        <p className="text-muted-foreground">
          No rules. Only the included keys are in the segment.
        </p>
      ) : (
        <div className="flex min-w-0 flex-col gap-4">
          {draft.rules.map((r, i) => (
            <RuleCard
              key={r.id}
              rule={r}
              index={i}
              count={draft.rules.length}
              readOnly={readOnly}
              problems={problems}
              moveButtons={moveButtons}
              onChange={(edit) => onChange(edit(draft))}
              onMove={(by) => {
                focusAfterMove.current = {
                  ruleId: r.id,
                  dir: by < 0 ? "up" : "down",
                };
                onChange(moveRule(draft, r.id, by));
              }}
              onRemove={() => onChange(removeRule(draft, r.id))}
            />
          ))}
        </div>
      )}
      {!readOnly && (
        <Button
          variant="outline"
          className="min-h-11 w-fit"
          onClick={() => onChange(addRule(draft))}
        >
          <Plus aria-hidden />
          Add rule
        </Button>
      )}
    </>
  );
}
