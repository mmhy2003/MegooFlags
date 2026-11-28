"""Evaluation through the real OpenFeature client, with an offline provider and a bootstrap
file."""

import itertools
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openfeature import api
from openfeature.client import OpenFeatureClient
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode, GeneralError, ProviderFatalError
from openfeature.flag_evaluation import Reason
from ruleset_gen import cl, flag_json, provider_ruleset, ruleset_json

from megooflags import MegooFlagsProvider

KEY = "mf_server_provider-secret-value"

_domains = itertools.count(1)


@pytest.fixture(autouse=True)
def _openfeature() -> Iterator[None]:
    yield
    api.shutdown()


@pytest.fixture
def bootstrap(tmp_path: Path) -> Path:
    path = tmp_path / "ruleset.json"
    path.write_text(provider_ruleset())
    return path


def offline(bootstrap_file: Path | None, **opts: Any) -> MegooFlagsProvider:
    return MegooFlagsProvider(sdk_key=KEY, bootstrap_file=bootstrap_file, offline=True, **opts)


def client_for(provider: MegooFlagsProvider) -> OpenFeatureClient:
    domain = f"provider-{next(_domains)}"
    api.set_provider_and_wait(provider, domain)
    return api.get_client(domain)


@pytest.fixture
def client(bootstrap: Path) -> OpenFeatureClient:
    return client_for(offline(bootstrap))


def test_names_itself_and_has_no_hooks() -> None:
    p = offline(None, cache_dir="x")
    assert p.get_metadata().name == "MegooFlags"
    assert p.get_provider_hooks() == []


def test_resolves_a_boolean_flag(client: OpenFeatureClient) -> None:
    d = client.get_boolean_details("bool-flag", True, EvaluationContext("u1", {"plan": "pro"}))
    assert (d.value, d.variant, d.reason, d.error_code) == (True, "on", "TARGETING_MATCH", None)
    assert d.flag_metadata == {"rule_index": 0}
    d = client.get_boolean_details("bool-flag", True, EvaluationContext("u1"))
    assert (d.value, d.variant, d.reason) == (False, "off", Reason.DEFAULT)


def test_resolves_a_string_flag(client: OpenFeatureClient) -> None:
    d = client.get_string_details("string-flag", "x")
    assert (d.value, d.variant, d.reason, d.error_code) == ("blue", "blue", "STATIC", None)


def test_resolves_a_number_as_a_float(client: OpenFeatureClient) -> None:
    d = client.get_float_details("number-flag", 1.0)
    assert (d.value, d.variant, d.error_code) == (2.5, "half", None)
    d = client.get_float_details("int-flag", 1.0)
    assert d.value == 7.0
    assert type(d.value) is float


def test_resolves_an_integral_number_as_an_int(client: OpenFeatureClient) -> None:
    d = client.get_integer_details("int-flag", 1)
    assert (d.value, d.variant, d.error_code) == (7, "seven", None)
    assert type(d.value) is int


def test_refuses_a_number_that_is_not_integral_as_an_int(client: OpenFeatureClient) -> None:
    d = client.get_integer_details("number-flag", 1)
    assert (d.value, d.reason, d.error_code) == (1, Reason.ERROR, ErrorCode.TYPE_MISMATCH)
    assert d.error_message


def test_resolves_an_object_flag(client: OpenFeatureClient) -> None:
    d = client.get_object_details("json-flag", {})
    assert d.value == {"limit": 10, "tags": ["a", "b"]}
    assert d.variant == "cfg"


def test_maps_the_targeting_key_to_the_key_attribute(client: OpenFeatureClient) -> None:
    d = client.get_string_details("targeted", "x", EvaluationContext("alice"))
    assert (d.value, d.reason) == ("hello alice", "TARGETING_MATCH")
    # The targeting key wins over a key attribute.
    ctx = EvaluationContext("alice", {"key": "bob"})
    assert client.get_string_value("targeted", "x", ctx) == "hello alice"
    ctx = EvaluationContext(None, {"key": "alice"})
    assert client.get_string_value("targeted", "x", ctx) == "hello alice"


def test_returns_the_default_with_flag_not_found(client: OpenFeatureClient) -> None:
    d = client.get_string_details("missing", "x")
    assert (d.value, d.reason, d.error_code) == ("x", Reason.ERROR, ErrorCode.FLAG_NOT_FOUND)
    assert d.error_message == "flag 'missing' not found"


def test_returns_the_default_with_type_mismatch(client: OpenFeatureClient) -> None:
    d = client.get_boolean_details("string-flag", False)
    assert (d.value, d.reason, d.error_code) == (False, Reason.ERROR, ErrorCode.TYPE_MISMATCH)
    d = client.get_float_details("bool-flag", 1.5)
    assert (d.value, d.error_code) == (1.5, ErrorCode.TYPE_MISMATCH)


def test_passes_the_prerequisite_failed_reason_and_metadata_through(
    client: OpenFeatureClient,
) -> None:
    d = client.get_boolean_details("needs-prereq", True)
    assert (d.value, d.variant, d.reason) == (False, "off", "PREREQUISITE_FAILED")
    assert d.flag_metadata == {"prerequisite": "prereq-off"}


def test_unwraps_nested_attributes(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    flag = flag_json(
        "nested",
        rules=[{"clauses": [cl("tags", "in", False, "beta")], "serve": {"variation": "on"}}],
        fallthrough={"variation": "off"},
    )
    path.write_text(json.dumps(ruleset_json([flag])))
    client = client_for(offline(path))
    ctx = EvaluationContext("u1", {"tags": ("alpha", "beta")})
    assert client.get_string_value("nested", "x", ctx) == "on"


def test_matches_nothing_with_a_date_attribute(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    flag = flag_json(
        "dated",
        rules=[
            {
                "clauses": [cl("signed_up", "before", False, "2030-01-01T00:00:00Z")],
                "serve": {"variation": "on"},
            }
        ],
        fallthrough={"variation": "off"},
    )
    path.write_text(json.dumps(ruleset_json([flag])))
    client = client_for(offline(path))
    ctx = EvaluationContext("u1", {"signed_up": datetime(2020, 1, 1, tzinfo=UTC)})
    assert client.get_string_value("dated", "x", ctx) == "off"
    ctx = EvaluationContext("u1", {"signed_up": "2020-01-01T00:00:00Z"})
    assert client.get_string_value("dated", "x", ctx) == "on"


def test_is_not_ready_without_a_ruleset(tmp_path: Path) -> None:
    p = offline(None, cache_dir=tmp_path)
    d = p.resolve_string_details("string-flag", "x")
    assert (d.value, d.reason, d.error_code) == ("x", Reason.ERROR, ErrorCode.PROVIDER_NOT_READY)
    with pytest.raises(GeneralError):
        api.set_provider_and_wait(p, f"provider-{next(_domains)}")
    d = p.resolve_integer_details("int-flag", 3)
    assert (d.value, d.error_code) == (3, ErrorCode.PROVIDER_NOT_READY)


def test_is_not_ready_after_an_invalid_bootstrap_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version": 99}')
    p = offline(path)
    with pytest.raises(GeneralError):
        api.set_provider_and_wait(p, f"provider-{next(_domains)}")
    assert p.resolve_boolean_details("bool-flag", True).error_code == ErrorCode.PROVIDER_NOT_READY
    assert "invalid ruleset" in caplog.text


def test_warns_about_a_missing_bootstrap_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = offline(tmp_path / "missing.json")
    assert p.resolve_boolean_details("bool-flag", True).error_code == ErrorCode.PROVIDER_NOT_READY
    assert "could not read the bootstrap file" in caplog.text


def test_evaluates_the_default_with_provider_fatal_for_an_invalid_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = MegooFlagsProvider(sdk_key="mf_client_secret-value", base_url="https://x.example")
    d = p.resolve_boolean_details("bool-flag", True)
    assert (d.value, d.reason, d.error_code) == (True, Reason.ERROR, ErrorCode.PROVIDER_FATAL)
    assert d.error_message is not None and "client SDK key" in d.error_message
    with pytest.raises(ProviderFatalError):
        api.set_provider_and_wait(p, f"provider-{next(_domains)}")
    assert "invalid config" in caplog.text
    assert "secret-value" not in caplog.text


def test_exposes_the_config() -> None:
    p = MegooFlagsProvider(sdk_key=KEY, base_url="https://x.example/", poll_interval=1)
    assert p.config.base_url == "https://x.example"
    assert p.config.poll_interval == 15.0
