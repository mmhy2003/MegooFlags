package megooflags

import (
	"context"
	"os"
	"sync/atomic"

	"github.com/open-feature/go-sdk/openfeature"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/delivery"
	"github.com/mmhy2003/MegooFlags/sdks/go/internal/engine"
	"github.com/mmhy2003/MegooFlags/sdks/go/internal/events"
)

const userAgent = "megooflags-go/" + Version

// Provider is the MegooFlags OpenFeature provider. It evaluates flags against
// the ruleset in its Store, with no network call per evaluation.
type Provider struct {
	cfg     Config
	cfgErr  error
	store   *delivery.Store
	sync    *delivery.Sync
	batcher *events.Batcher
	events  chan openfeature.Event
	fatal   atomic.Bool
}

var _ openfeature.FeatureProvider = (*Provider)(nil)

// NewProvider validates cfg and loads cfg.BootstrapFile. It never touches the
// network. An invalid config is logged, and every evaluation then returns the
// default with PROVIDER_FATAL.
func NewProvider(cfg Config) *Provider {
	cfg = cfg.withDefaults()
	p := &Provider{
		cfg:    cfg,
		store:  &delivery.Store{},
		events: make(chan openfeature.Event, 16),
	}
	if err := cfg.validate(); err != nil {
		p.cfgErr = err
		p.fatal.Store(true)
		cfg.Logger.Error("megooflags: invalid config", "error", err)
		return p
	}
	p.sync = delivery.NewSync(delivery.SyncConfig{
		BaseURL:       cfg.BaseURL,
		Key:           cfg.SDKKey,
		UserAgent:     userAgent,
		Client:        cfg.HTTPClient,
		Logger:        cfg.Logger,
		DisableStream: cfg.DisableStream,
		PollInterval:  cfg.PollInterval,
		Store:         p.store,
		Cache:         delivery.NewCache(cfg.CacheDir, cfg.SDKKey),
	})
	if cfg.BootstrapFile != "" {
		raw, err := os.ReadFile(cfg.BootstrapFile)
		if err != nil {
			cfg.Logger.Warn("megooflags: could not read the bootstrap file", "error", err)
		} else {
			_ = p.sync.Apply(raw, "", "bootstrap") // Apply logs an invalid ruleset
		}
	}
	return p
}

// Metadata names the provider.
func (p *Provider) Metadata() openfeature.Metadata {
	return openfeature.Metadata{Name: "MegooFlags"}
}

// Hooks returns no provider hooks.
func (p *Provider) Hooks() []openfeature.Hook { return nil }

// BooleanEvaluation resolves a boolean flag.
func (p *Provider) BooleanEvaluation(_ context.Context, flag string, def bool, flatCtx openfeature.FlattenedContext) openfeature.BoolResolutionDetail {
	res, ok := p.evaluate(flag, def, flatCtx, "boolean")
	if !ok {
		return openfeature.BoolResolutionDetail{Value: def, ProviderResolutionDetail: p.notReady()}
	}
	v, isBool := res.Value.(bool)
	if res.ErrorCode != engine.ErrNone || !isBool {
		v = def
	}
	return openfeature.BoolResolutionDetail{Value: v, ProviderResolutionDetail: detail(res)}
}

// StringEvaluation resolves a string flag.
func (p *Provider) StringEvaluation(_ context.Context, flag string, def string, flatCtx openfeature.FlattenedContext) openfeature.StringResolutionDetail {
	res, ok := p.evaluate(flag, def, flatCtx, "string")
	if !ok {
		return openfeature.StringResolutionDetail{Value: def, ProviderResolutionDetail: p.notReady()}
	}
	v, isString := res.Value.(string)
	if res.ErrorCode != engine.ErrNone || !isString {
		v = def
	}
	return openfeature.StringResolutionDetail{Value: v, ProviderResolutionDetail: detail(res)}
}

// FloatEvaluation resolves a number flag as a float64.
func (p *Provider) FloatEvaluation(_ context.Context, flag string, def float64, flatCtx openfeature.FlattenedContext) openfeature.FloatResolutionDetail {
	res, ok := p.evaluate(flag, def, flatCtx, "number")
	if !ok {
		return openfeature.FloatResolutionDetail{Value: def, ProviderResolutionDetail: p.notReady()}
	}
	v, isNumber := toFloat64(res)
	if res.ErrorCode != engine.ErrNone || !isNumber {
		v = def
	}
	return openfeature.FloatResolutionDetail{Value: v, ProviderResolutionDetail: detail(res)}
}

// IntEvaluation resolves a number flag as an int64. A number that is not an
// integer (or does not fit int64) is a TYPE_MISMATCH.
func (p *Provider) IntEvaluation(_ context.Context, flag string, def int64, flatCtx openfeature.FlattenedContext) openfeature.IntResolutionDetail {
	res, ok := p.evaluate(flag, def, flatCtx, "number")
	if !ok {
		return openfeature.IntResolutionDetail{Value: def, ProviderResolutionDetail: p.notReady()}
	}
	if res.ErrorCode != engine.ErrNone || !res.Served {
		return openfeature.IntResolutionDetail{Value: def, ProviderResolutionDetail: detail(res)}
	}
	if !res.IsInt {
		return openfeature.IntResolutionDetail{
			Value: def,
			ProviderResolutionDetail: openfeature.ProviderResolutionDetail{
				ResolutionError: openfeature.NewTypeMismatchResolutionError("not an integer"),
				Reason:          openfeature.ErrorReason,
			},
		}
	}
	return openfeature.IntResolutionDetail{Value: res.Int, ProviderResolutionDetail: detail(res)}
}

// ObjectEvaluation resolves a flag of any type; json flags are the intended
// use.
func (p *Provider) ObjectEvaluation(_ context.Context, flag string, def any, flatCtx openfeature.FlattenedContext) openfeature.InterfaceResolutionDetail {
	res, ok := p.evaluate(flag, def, flatCtx, "json")
	if !ok {
		return openfeature.InterfaceResolutionDetail{Value: def, ProviderResolutionDetail: p.notReady()}
	}
	v := res.Value
	if res.ErrorCode != engine.ErrNone {
		v = def
	}
	return openfeature.InterfaceResolutionDetail{Value: v, ProviderResolutionDetail: detail(res)}
}

// evaluate runs the engine on the current ruleset; ok is false when there is
// none yet. The context is copied, with targetingKey mapped to key.
func (p *Provider) evaluate(flag string, def any, flatCtx openfeature.FlattenedContext, expectedType string) (engine.Result, bool) {
	snap := p.store.Load()
	if snap == nil {
		return engine.Result{}, false
	}
	ctx := make(map[string]any, len(flatCtx)+1)
	for k, v := range flatCtx {
		ctx[k] = v
	}
	if tk, ok := flatCtx[openfeature.TargetingKey]; ok {
		ctx["key"] = tk
	}
	res := engine.Evaluate(snap.Ruleset, flag, ctx, def, expectedType)
	p.record(flag, res, ctx)
	return res, true
}

// record enqueues the evaluation for the batcher. It is wired in with the
// lifecycle.
func (p *Provider) record(string, engine.Result, map[string]any) {}

// notReady is the detail returned while there is no ruleset, or after a fatal
// error.
func (p *Provider) notReady() openfeature.ProviderResolutionDetail {
	var re openfeature.ResolutionError
	switch {
	case p.cfgErr != nil:
		re = openfeature.NewProviderFatalResolutionError(p.cfgErr.Error())
	case p.fatal.Load():
		re = openfeature.NewProviderFatalResolutionError("megooflags: the SDK key was refused")
	default:
		re = openfeature.NewProviderNotReadyResolutionError("megooflags: no ruleset is loaded yet")
	}
	return openfeature.ProviderResolutionDetail{ResolutionError: re, Reason: openfeature.ErrorReason}
}

// detail maps an engine result to OpenFeature. PREREQUISITE_FAILED passes
// through as a custom reason.
func detail(res engine.Result) openfeature.ProviderResolutionDetail {
	d := openfeature.ProviderResolutionDetail{
		Reason:       openfeature.Reason(res.Reason),
		Variant:      res.Variant,
		FlagMetadata: openfeature.FlagMetadata(res.Metadata()),
	}
	switch res.ErrorCode {
	case engine.ErrNone:
	case engine.ErrFlagNotFound:
		d.ResolutionError = openfeature.NewFlagNotFoundResolutionError(res.ErrorMessage)
	case engine.ErrTypeMismatch:
		d.ResolutionError = openfeature.NewTypeMismatchResolutionError(res.ErrorMessage)
	case engine.ErrInvalidContext:
		d.ResolutionError = openfeature.NewInvalidContextResolutionError(res.ErrorMessage)
	case engine.ErrParse:
		d.ResolutionError = openfeature.NewParseErrorResolutionError(res.ErrorMessage)
	default:
		d.ResolutionError = openfeature.NewGeneralResolutionError(res.ErrorMessage)
	}
	return d
}

// toFloat64 returns a served number as a float64.
func toFloat64(res engine.Result) (float64, bool) {
	if res.IsInt {
		return float64(res.Int), true
	}
	f, ok := res.Value.(float64)
	return f, ok
}
