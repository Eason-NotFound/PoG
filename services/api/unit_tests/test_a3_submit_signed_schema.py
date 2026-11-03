"""Connection-free input-shape checks for the explicitly authorized bridge."""
import pytest
from pydantic import ValidationError

from pog_api.schemas import SubmitSignedRequest


def test_submit_signed_schema_accepts_only_the_explicit_json_true():
    assert SubmitSignedRequest.model_validate({"confirm": True}).model_dump() == {"confirm": True}


@pytest.mark.parametrize("value", [False, 0, 1, 1.0, "true", "True", None, [], {}])
def test_submit_signed_confirmation_is_not_coerced(value):
    with pytest.raises(ValidationError):
        SubmitSignedRequest.model_validate({"confirm": value})


@pytest.mark.parametrize("body", [
    {}, {"confirm": True, "signature": "not-a-signature"},
    {"confirm": True, "caller": "not-a-wallet"},
    {"confirm": True, "rpcUrl": "http://example.invalid"},
    {"confirm": True, "reserveAmountAtomic": "1"},
    {"confirm": True, "requestId": "another-target"},
])
def test_submit_signed_schema_has_no_implicit_material_or_target_inputs(body):
    with pytest.raises(ValidationError):
        SubmitSignedRequest.model_validate(body)
