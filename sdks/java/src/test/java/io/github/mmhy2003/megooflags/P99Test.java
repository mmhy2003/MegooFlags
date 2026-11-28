package io.github.mmhy2003.megooflags;

import static org.junit.jupiter.api.Assertions.assertTrue;

import io.github.mmhy2003.megooflags.internal.engine.Evaluator;
import io.github.mmhy2003.megooflags.internal.engine.Ruleset;
import io.github.mmhy2003.megooflags.internal.engine.RulesetParser;
import io.github.mmhy2003.megooflags.internal.json.Json;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * p99 guard: evaluation stays under 1 ms on 10 000 flags. It runs alone (mvn test -Pp99) so other
 * tests do not compete for the CPU.
 */
@Tag("p99")
class P99Test {
    private static final int FLAGS = 10000;
    private static final int WARM_UP = 10000;
    private static final int SAMPLES = 100000;

    @Test
    void evaluates10000FlagsWithP99Under1Ms() throws Exception {
        Ruleset rs = RulesetParser.parse(Json.write(RulesetGen.genRuleset(FLAGS)));
        List<String> keys = RulesetGen.flagKeys(FLAGS);
        List<Map<String, Object>> ctxs = RulesetGen.benchContexts();
        Random random = new Random(12345);
        for (int i = 0; i < WARM_UP; i++) {
            Evaluator.evaluate(rs, keys.get(random.nextInt(FLAGS)), ctxs.get(random.nextInt(ctxs.size())), null, null);
        }
        long[] samples = new long[SAMPLES];
        for (int i = 0; i < SAMPLES; i++) {
            String key = keys.get(random.nextInt(FLAGS));
            Map<String, Object> ctx = ctxs.get(random.nextInt(ctxs.size()));
            long start = System.nanoTime();
            Evaluator.evaluate(rs, key, ctx, null, null);
            samples[i] = System.nanoTime() - start;
        }
        Arrays.sort(samples);
        double p50 = samples[SAMPLES / 2] / 1e6;
        double p99 = samples[SAMPLES * 99 / 100] / 1e6;
        double max = samples[SAMPLES - 1] / 1e6;
        System.out.println(String.format(Locale.ROOT, "p50 %.4f ms, p99 %.4f ms, max %.4f ms", p50, p99, max));
        assertTrue(p99 < 1, "p99 is " + p99 + " ms");
    }
}
