// p99 guard: evaluation stays under 1 ms on 10 000 flags. It runs alone
// (pnpm test:p99) so other tests do not compete for the CPU.
import { expect, it } from "vitest";
import { evaluate } from "../src/internal/engine/evaluate.js";
import { parseRuleset } from "../src/internal/engine/parse.js";
import {
  benchContexts,
  flagKeys,
  genRuleset,
} from "../test/helpers/ruleset.js";

const FLAGS = 10000;
const WARM_UP = 10000;
const SAMPLES = 100000;

/** mulberry32 is a small seeded PRNG returning integers in [0, n). */
function mulberry32(seed: number): (n: number) => number {
  let a = seed >>> 0;
  return (n) => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return Math.floor((((t ^ (t >>> 14)) >>> 0) / 4294967296) * n);
  };
}

it("evaluates 10 000 flags with p99 under 1 ms", { timeout: 120_000 }, () => {
  const rs = parseRuleset(JSON.stringify(genRuleset(FLAGS)));
  const keys = flagKeys(FLAGS);
  const ctxs = benchContexts();
  const next = mulberry32(12345);
  const pick = () => [keys[next(FLAGS)]!, ctxs[next(ctxs.length)]!] as const;
  for (let i = 0; i < WARM_UP; i++) {
    const [key, ctx] = pick();
    evaluate(rs, key, ctx, null);
  }
  const samples = new Float64Array(SAMPLES);
  for (let i = 0; i < SAMPLES; i++) {
    const [key, ctx] = pick();
    const start = process.hrtime.bigint();
    evaluate(rs, key, ctx, null);
    samples[i] = Number(process.hrtime.bigint() - start) / 1e6;
  }
  samples.sort();
  const p50 = samples[SAMPLES / 2]!;
  const p99 = samples[(SAMPLES * 99) / 100]!;
  const max = samples[SAMPLES - 1]!;
  const ms = (x: number) => `${x.toFixed(4)} ms`;
  console.log(`p50 ${ms(p50)}, p99 ${ms(p99)}, max ${ms(max)}`);
  expect(p99).toBeLessThan(1);
});
