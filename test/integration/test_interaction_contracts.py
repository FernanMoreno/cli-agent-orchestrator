"""Check production ledger projections against the shipped OpenAPI schemas."""

from pathlib import Path
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401

import jsonschema
import pytest
import yaml
from fastapi.encoders import jsonable_encoder

pytestmark = pytest.mark.postgres

_CONTRACT = yaml.safe_load(
    (
        Path(__file__).resolve().parents[2] / "specs/016-agent-interactions/contracts/http-api.yaml"
    ).read_text()
)


def _validate(schema_name, value):
    value = jsonable_encoder(value)
    jsonschema.Draft202012Validator(_CONTRACT).evolve(
        schema={"$ref": f"#/components/schemas/{schema_name}"}
    ).validate(value)


def _question(context):
    created = context.client.post(
        "/agent/interactions/groups",
        headers={
            "X-CAO-Interaction-Capability": context.source_token,
            "Idempotency-Key": "contract-question-operation-0001",
        },
        json={
            "questions": [{"kind": "text", "prompt": "contract-private-content", "required": True}]
        },
    )
    assert created.status_code == 201, created.text
    _validate("CreatedGroup", created.json())
    return created.json()["questions"][0]["question_id"]


def test_protected_detail_matches_strict_consumer_contract(interaction_http_context):
    context = interaction_http_context
    question_id = _question(context)
    response = context.client.get(f"/interactions/{question_id}")
    assert response.status_code == 200, response.text
    _validate("InteractionDetail", response.json())


def test_metadata_page_matches_consumer_contract(interaction_http_context):
    context = interaction_http_context
    _question(context)
    response = context.client.get("/interactions", params={"limit": 100})
    assert response.status_code == 200, response.text
    metadata = response.json()
    for item in metadata["items"]:
        _validate("InteractionMetadata", item)


def test_provider_capabilities_do_not_claim_unproven_native_support(interaction_http_context):
    context = interaction_http_context
    response = context.client.get(
        "/interaction-capabilities",
        headers={"X-CAO-Interaction-Capability": context.source_token},
    )
    assert response.status_code == 200, response.text
    capabilities = response.json()
    for item in capabilities["items"]:
        _validate("ProviderCapability", item)
        assert item["responds_native"] == []
        assert item["manual_reason"]
