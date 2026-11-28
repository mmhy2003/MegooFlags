package io.github.mmhy2003.megooflags;

import static io.github.mmhy2003.megooflags.RulesetGen.cl;
import static io.github.mmhy2003.megooflags.RulesetGen.ruleFlag;
import static io.github.mmhy2003.megooflags.RulesetGen.rulesetJson;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import dev.openfeature.sdk.Client;
import dev.openfeature.sdk.ErrorCode;
import dev.openfeature.sdk.EvaluationContext;
import dev.openfeature.sdk.FlagEvaluationDetails;
import dev.openfeature.sdk.ImmutableContext;
import dev.openfeature.sdk.MutableContext;
import dev.openfeature.sdk.OpenFeatureAPI;
import dev.openfeature.sdk.ProviderEvaluation;
import dev.openfeature.sdk.Reason;
import dev.openfeature.sdk.Value;
import dev.openfeature.sdk.exceptions.FatalError;
import dev.openfeature.sdk.exceptions.GeneralError;
import dev.openfeature.sdk.exceptions.OpenFeatureError;
import io.github.mmhy2003.megooflags.internal.BuildInfo;
import io.github.mmhy2003.megooflags.internal.json.Json;
import java.io.IOException;
import java.lang.System.Logger.Level;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class MegooFlagsProviderTest {
    private static final String KEY = "mf_server_provider-secret-value";
    private static final AtomicInteger DOMAINS = new AtomicInteger();

    @TempDir
    Path dir;

    private Path bootstrapFile;
    private final OpenFeatureAPI api = OpenFeatureAPI.getInstance();

    @BeforeEach
    void writeBootstrapFile() throws IOException {
        bootstrapFile = dir.resolve("ruleset.json");
        Files.writeString(bootstrapFile, RulesetGen.providerRuleset());
    }

    @AfterEach
    void shutdownApi() {
        api.shutdown();
    }

    private MegooFlagsConfig.Builder offline() {
        return MegooFlagsConfig.builder()
                .sdkKey(KEY)
                .offline(true)
                .bootstrapFile(bootstrapFile)
                .logger(new RecordingLogger());
    }

    /** client registers provider under a new domain and waits for it. */
    private Client client(MegooFlagsProvider provider) {
        String domain = "provider-" + DOMAINS.incrementAndGet();
        api.setProviderAndWait(domain, provider);
        return api.getClient(domain);
    }

    private Client client() {
        return client(new MegooFlagsProvider(offline().build()));
    }

    private static EvaluationContext ctx(String key, Object... attributes) {
        MutableContext c = key == null ? new MutableContext() : new MutableContext(key);
        for (int i = 0; i < attributes.length; i += 2) {
            String name = (String) attributes[i];
            Object v = attributes[i + 1];
            if (v instanceof String) {
                c.add(name, (String) v);
            } else if (v instanceof Instant) {
                c.add(name, (Instant) v);
            } else if (v instanceof Integer) {
                c.add(name, (Integer) v);
            } else {
                throw new IllegalArgumentException(name);
            }
        }
        return c;
    }

    @Test
    void namesItselfAndExposesTheVersion() {
        MegooFlagsProvider p = new MegooFlagsProvider(offline().build());
        assertEquals("MegooFlags", p.getMetadata().getName());
        assertEquals("0.1.0", MegooFlagsProvider.VERSION);
        assertEquals(BuildInfo.VERSION, MegooFlagsProvider.VERSION);
    }

    @Test
    void resolvesABooleanFlag() {
        Client c = client();
        FlagEvaluationDetails<Boolean> pro = c.getBooleanDetails("bool-flag", false, ctx(null, "plan", "pro"));
        assertEquals(true, pro.getValue());
        assertEquals("on", pro.getVariant());
        assertEquals(Reason.TARGETING_MATCH.toString(), pro.getReason());
        assertNull(pro.getErrorCode());
        assertEquals(0, pro.getFlagMetadata().getInteger("rule_index"));
        assertNull(pro.getFlagMetadata().getString("prerequisite"));

        FlagEvaluationDetails<Boolean> free = c.getBooleanDetails("bool-flag", true, new ImmutableContext());
        assertEquals(false, free.getValue());
        assertEquals("off", free.getVariant());
        assertEquals(Reason.DEFAULT.toString(), free.getReason());
        assertTrue(free.getFlagMetadata().isEmpty());
    }

    @Test
    void resolvesAStringFlag() {
        FlagEvaluationDetails<String> d = client().getStringDetails("string-flag", "x");
        assertEquals("blue", d.getValue());
        assertEquals("blue", d.getVariant());
        assertEquals(Reason.STATIC.toString(), d.getReason());
    }

    @Test
    void resolvesADoubleFlag() {
        Client c = client();
        FlagEvaluationDetails<Double> d = c.getDoubleDetails("number-flag", 1.0);
        assertEquals(2.5, d.getValue());
        assertEquals("half", d.getVariant());
        assertEquals(3.0, c.getDoubleValue("int-flag", 1.0));
    }

    @Test
    void resolvesAnIntegerFlagWithAnIntegralValueInTheIntRange() {
        Client c = client();
        FlagEvaluationDetails<Integer> three = c.getIntegerDetails("int-flag", 7);
        assertEquals(3, three.getValue());
        assertEquals("three", three.getVariant());
        assertNull(three.getErrorCode());
        assertEquals(2, c.getIntegerValue("int-flag", 7, ctx("two")));

        for (String key : List.of("half", "big")) {
            FlagEvaluationDetails<Integer> d = c.getIntegerDetails("int-flag", 7, ctx(key));
            assertEquals(7, d.getValue(), key);
            assertEquals(ErrorCode.TYPE_MISMATCH, d.getErrorCode(), key);
            assertEquals(Reason.ERROR.toString(), d.getReason(), key);
        }
        FlagEvaluationDetails<Integer> half = c.getIntegerDetails("number-flag", 7);
        assertEquals(7, half.getValue());
        assertEquals(ErrorCode.TYPE_MISMATCH, half.getErrorCode());
    }

    @Test
    void resolvesAnObjectFlag() {
        FlagEvaluationDetails<Value> d = client().getObjectDetails("json-flag", new Value("dflt"));
        assertEquals("cfg", d.getVariant());
        Map<String, Value> m = d.getValue().asStructure().asMap();
        assertEquals(new Value(10), m.get("limit"));
        assertInstanceOf(Integer.class, m.get("limit").asObject());
        assertEquals(new Value(3_000_000_000L), m.get("big"));
        assertEquals(new Value(0.5), m.get("ratio"));
        assertEquals(List.of(new Value("a"), new Value("b")), m.get("tags").asList());
    }

    @Test
    void mapsTheTargetingKeyToTheKeyAttribute() {
        Client c = client();
        FlagEvaluationDetails<String> alice = c.getStringDetails("targeted", "x", ctx("alice"));
        assertEquals("hello alice", alice.getValue());
        assertEquals("alice", alice.getVariant());
        assertEquals(Reason.TARGETING_MATCH.toString(), alice.getReason());
        assertEquals("hello", c.getStringValue("targeted", "x", ctx("bob", "key", "alice")));
        assertEquals("hello alice", c.getStringValue("targeted", "x", ctx(null, "key", "alice")));
    }

    @Test
    void returnsTheDefaultWithFlagNotFoundForAMissingFlag() {
        FlagEvaluationDetails<String> d = client().getStringDetails("missing", "fallback");
        assertEquals("fallback", d.getValue());
        assertEquals(Reason.ERROR.toString(), d.getReason());
        assertEquals(ErrorCode.FLAG_NOT_FOUND, d.getErrorCode());
        assertTrue(d.getErrorMessage().contains("missing"), d.getErrorMessage());
    }

    @Test
    void returnsTheDefaultWithTypeMismatchForTheWrongType() {
        Client c = client();
        FlagEvaluationDetails<Boolean> b = c.getBooleanDetails("string-flag", true);
        assertEquals(true, b.getValue());
        assertEquals(Reason.ERROR.toString(), b.getReason());
        assertEquals(ErrorCode.TYPE_MISMATCH, b.getErrorCode());
        FlagEvaluationDetails<Double> n = c.getDoubleDetails("json-flag", 7.0);
        assertEquals(7.0, n.getValue());
        assertEquals(ErrorCode.TYPE_MISMATCH, n.getErrorCode());
    }

    @Test
    void returnsTheDefaultItselfOnAnError() {
        MegooFlagsProvider p = new MegooFlagsProvider(offline().build());
        ProviderEvaluation<String> d = p.getStringEvaluation("missing", "fallback", new ImmutableContext());
        assertEquals("fallback", d.getValue());
        assertEquals(Reason.ERROR.toString(), d.getReason());
        assertEquals(ErrorCode.FLAG_NOT_FOUND, d.getErrorCode());
        ProviderEvaluation<Integer> i = p.getIntegerEvaluation("number-flag", 7, new ImmutableContext());
        assertEquals(7, i.getValue());
        assertEquals(ErrorCode.TYPE_MISMATCH, i.getErrorCode());
        assertEquals("half", i.getVariant());
    }

    @Test
    void passesThePrerequisiteFailedReasonAndMetadataThrough() {
        FlagEvaluationDetails<Boolean> d = client().getBooleanDetails("needs-prereq", true);
        assertEquals(false, d.getValue());
        assertEquals("off", d.getVariant());
        assertEquals("PREREQUISITE_FAILED", d.getReason());
        assertEquals("prereq-off", d.getFlagMetadata().getString("prerequisite"));
        assertNull(d.getErrorCode());
    }

    @Test
    void comparesIntegerAttributesAsNumbers() throws IOException {
        Path file = dir.resolve("numbers.json");
        Files.writeString(
                file, Json.write(rulesetJson(List.of(ruleFlag("seats", cl("seats", "gte", false, 10L))), Map.of())));
        Client c = client(new MegooFlagsProvider(offline().bootstrapFile(file).build()));
        assertEquals("on", c.getStringValue("seats", "d", ctx(null, "seats", 12)));
        assertEquals("off", c.getStringValue("seats", "d", ctx(null, "seats", 9)));
    }

    @Test
    void matchesNothingWithADateAttribute() {
        FlagEvaluationDetails<Boolean> d =
                client().getBooleanDetails("bool-flag", true, ctx(null, "plan", Instant.now()));
        assertEquals(false, d.getValue());
        assertEquals("off", d.getVariant());
    }

    @Test
    void isNotReadyWithoutARuleset() {
        MegooFlagsProvider p = new MegooFlagsProvider(
                offline().bootstrapFile(dir.resolve("missing.json")).build());
        ProviderEvaluation<String> d = p.getStringEvaluation("string-flag", "x", new ImmutableContext());
        assertEquals("x", d.getValue());
        assertEquals(Reason.ERROR.toString(), d.getReason());
        assertEquals(ErrorCode.PROVIDER_NOT_READY, d.getErrorCode());
    }

    @Test
    void isNotReadyAfterAnInvalidBootstrapFile() throws IOException {
        Path file = dir.resolve("invalid.json");
        Files.writeString(file, "{\"schema_version\": 99}");
        RecordingLogger log = new RecordingLogger();
        MegooFlagsProvider p =
                new MegooFlagsProvider(offline().bootstrapFile(file).logger(log).build());
        String domain = "provider-" + DOMAINS.incrementAndGet();
        OpenFeatureError err = assertThrows(OpenFeatureError.class, () -> api.setProviderAndWait(domain, p));
        assertInstanceOf(GeneralError.class, err);
        ProviderEvaluation<String> d = p.getStringEvaluation("string-flag", "x", new ImmutableContext());
        assertEquals("x", d.getValue());
        assertEquals(ErrorCode.PROVIDER_NOT_READY, d.getErrorCode());
        assertFalse(log.at(Level.WARNING).isEmpty());
    }

    @Test
    void evaluatesTheDefaultWithProviderFatalForAnInvalidConfig() {
        RecordingLogger log = new RecordingLogger();
        MegooFlagsProvider p = new MegooFlagsProvider(MegooFlagsConfig.builder()
                .sdkKey("mf_client_provider-secret")
                .baseUrl("http://127.0.0.1:1")
                .logger(log)
                .build());
        ProviderEvaluation<Boolean> direct = p.getBooleanEvaluation("bool-flag", true, new ImmutableContext());
        assertEquals(true, direct.getValue());
        assertEquals(Reason.ERROR.toString(), direct.getReason());
        assertEquals(ErrorCode.PROVIDER_FATAL, direct.getErrorCode());
        assertTrue(direct.getErrorMessage().contains("client SDK key"), direct.getErrorMessage());

        String domain = "provider-" + DOMAINS.incrementAndGet();
        OpenFeatureError err = assertThrows(OpenFeatureError.class, () -> api.setProviderAndWait(domain, p));
        assertInstanceOf(FatalError.class, err);
        assertTrue(err.getMessage().contains("client SDK key"), err.getMessage());
        FlagEvaluationDetails<Boolean> d = api.getClient(domain).getBooleanDetails("bool-flag", true);
        assertEquals(true, d.getValue());
        assertEquals(ErrorCode.PROVIDER_FATAL, d.getErrorCode());
        assertEquals(1, log.at(Level.ERROR).size());
        assertFalse(log.lines().toString().contains("provider-secret"));
    }
}
