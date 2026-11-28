package provider

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/attr/xattr"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/types/basetypes"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
)

var (
	_ basetypes.StringTypable                    = jsonValueType{}
	_ basetypes.StringValuableWithSemanticEquals = jsonValue{}
	_ xattr.ValidateableAttribute                = jsonValue{}
)

// jsonValueType is a string holding one JSON value. Like
// jsontypes.Normalized it ignores whitespace and object key order, and it
// also compares numbers by value, so 1 equals 1.0 (the API may return
// either).
type jsonValueType struct {
	basetypes.StringType
}

func (t jsonValueType) Equal(o attr.Type) bool {
	_, ok := o.(jsonValueType)
	return ok
}

func (t jsonValueType) String() string { return "jsonValueType" }

func (t jsonValueType) ValueType(context.Context) attr.Value { return jsonValue{} }

func (t jsonValueType) ValueFromString(_ context.Context, in basetypes.StringValue) (basetypes.StringValuable, diag.Diagnostics) {
	return jsonValue{StringValue: in}, nil
}

func (t jsonValueType) ValueFromTerraform(ctx context.Context, in tftypes.Value) (attr.Value, error) {
	v, err := t.StringType.ValueFromTerraform(ctx, in)
	if err != nil {
		return nil, err
	}
	s, ok := v.(basetypes.StringValue)
	if !ok {
		return nil, fmt.Errorf("unexpected value type %T", v)
	}
	return jsonValue{StringValue: s}, nil
}

// jsonValue is a value of jsonValueType.
type jsonValue struct {
	basetypes.StringValue
}

func newJSONValue(s string) jsonValue { return jsonValue{StringValue: basetypes.NewStringValue(s)} }

func (v jsonValue) Type(context.Context) attr.Type { return jsonValueType{} }

func (v jsonValue) Equal(o attr.Value) bool {
	other, ok := o.(jsonValue)
	return ok && v.StringValue.Equal(other.StringValue)
}

func (v jsonValue) StringSemanticEquals(_ context.Context, newValuable basetypes.StringValuable) (bool, diag.Diagnostics) {
	var diags diag.Diagnostics
	other, ok := newValuable.(jsonValue)
	if !ok {
		diags.AddError("Semantic equality check error", fmt.Sprintf("Expected jsonValue, got %T; this is a bug in the provider.", newValuable))
		return false, diags
	}
	equal, err := jsonEqual(v.ValueString(), other.ValueString())
	if err != nil {
		diags.AddError("Semantic equality check error", err.Error())
	}
	return equal, diags
}

func (v jsonValue) ValidateAttribute(_ context.Context, req xattr.ValidateAttributeRequest, resp *xattr.ValidateAttributeResponse) {
	if v.IsNull() || v.IsUnknown() {
		return
	}
	if _, err := decodeJSON(v.ValueString()); err != nil {
		resp.Diagnostics.AddAttributeError(req.Path, "Invalid JSON value",
			"Use jsonencode() to set a JSON value: "+err.Error())
	}
}

// decodeJSON decodes s, which must be exactly one JSON value. Numbers stay
// json.Number, so they are sent back to the API unchanged.
func decodeJSON(s string) (any, error) {
	dec := json.NewDecoder(strings.NewReader(s))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errors.New("unexpected data after the JSON value")
	}
	return v, nil
}

// encodeJSON is the JSON text of an API value.
func encodeJSON(v any) (string, error) {
	b, err := json.Marshal(v)
	return string(b), err
}

// jsonEqual reports whether a and b are the same JSON value.
func jsonEqual(a, b string) (bool, error) {
	va, err := decodeJSON(a)
	if err != nil {
		return false, err
	}
	vb, err := decodeJSON(b)
	if err != nil {
		return false, err
	}
	return sameJSON(va, vb), nil
}

func sameJSON(a, b any) bool {
	switch a := a.(type) {
	case json.Number:
		b, ok := b.(json.Number)
		if !ok {
			return false
		}
		ra, okA := new(big.Rat).SetString(a.String())
		rb, okB := new(big.Rat).SetString(b.String())
		return okA && okB && ra.Cmp(rb) == 0
	case []any:
		b, ok := b.([]any)
		if !ok || len(a) != len(b) {
			return false
		}
		for i := range a {
			if !sameJSON(a[i], b[i]) {
				return false
			}
		}
		return true
	case map[string]any:
		b, ok := b.(map[string]any)
		if !ok || len(a) != len(b) {
			return false
		}
		for k, va := range a {
			vb, ok := b[k]
			if !ok || !sameJSON(va, vb) {
				return false
			}
		}
		return true
	default: // string, bool or nil
		return a == b
	}
}
