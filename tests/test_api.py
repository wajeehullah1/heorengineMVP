"""Tests for the HEOR Engine FastAPI endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from engines.bia.cost_translator import BAND_RATES

# ── Fixture: realistic BIA inputs ──────────────────────────────────────

VALID_INPUTS = {
    "setting": "Acute NHS Trust",
    "model_year": 2026,
    "forecast_years": 3,
    "funding_source": "Trust operational budget",
    "catchment_type": "population",
    "catchment_size": 250000,
    "eligible_pct": 5.0,
    "uptake_y1": 20,
    "uptake_y2": 50,
    "uptake_y3": 80,
    "prevalence": "12/100,000 incidence; rising trend",
    "workforce": [
        {"role": "Band 5 (Staff Nurse)", "minutes": 30, "frequency": "per patient"},
        {"role": "Consultant", "minutes": 15, "frequency": "per patient"},
        {"role": "Admin/Clerical", "minutes": 10, "frequency": "per patient"},
    ],
    "outpatient_visits": 4,
    "tests": 2,
    "admissions": 1,
    "bed_days": 3,
    "procedures": 0,
    "consumables": 45.0,
    "pricing_model": "per-patient",
    "price": 1200.0,
    "price_unit": "per year",
    "needs_training": True,
    "training_roles": "Band 5 nurses, registrars",
    "training_hours": 2.0,
    "setup_cost": 5000.0,
    "staff_time_saved": 15.0,
    "visits_reduced": 20.0,
    "complications_reduced": 30.0,
    "readmissions_reduced": 15.0,
    "los_reduced": 1.0,
    "follow_up_reduced": 25.0,
    "comparator": "none",
    "comparator_names": "Paper-based triage",
    "discounting": "off",
}


@pytest.fixture
def transport():
    return ASGITransport(app=app)


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_health_returns_200(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_save_valid_inputs(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/inputs", json=VALID_INPUTS)

    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "saved"
    assert "id" in body
    assert "workforce_cost_per_patient" in body
    assert isinstance(body["workforce_cost_per_patient"], float)
    assert body["workforce_cost_per_patient"] > 0


@pytest.mark.anyio
async def test_workforce_cost_matches_manual(transport):
    """Verify the API workforce cost equals a hand-calculated value."""
    # Manual calculation:
    #   Band 5: 21.37 * 30/60 = 10.685
    #   Consultant: 72.00 * 15/60 = 18.00
    #   Admin/Clerical: 11.90 * 10/60 = 1.9833...
    #   Total = 30.6683... -> rounded to 30.67
    expected = round(
        BAND_RATES["Band 5 (Staff Nurse)"] * 30 / 60
        + BAND_RATES["Consultant"] * 15 / 60
        + BAND_RATES["Admin/Clerical"] * 10 / 60,
        2,
    )

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/inputs", json=VALID_INPUTS)

    cost = resp.json()["workforce_cost_per_patient"]
    print(f"\n  Manual calculation: £{expected}")
    print(f"  API returned:      £{cost}")
    assert cost == expected


@pytest.mark.anyio
async def test_reject_missing_required_fields(transport):
    """Omitting required fields should return 422."""
    incomplete = {"setting": "Acute NHS Trust"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/inputs", json=incomplete)

    assert resp.status_code == 422
    errors = resp.json()["detail"]
    missing_fields = {e["loc"][-1] for e in errors}
    assert "model_year" in missing_fields
    assert "forecast_years" in missing_fields
    assert "funding_source" in missing_fields


@pytest.mark.anyio
async def test_reject_invalid_enum_value(transport):
    """An invalid enum value should return 422."""
    bad = {**VALID_INPUTS, "setting": "GP Surgery"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/inputs", json=bad)

    assert resp.status_code == 422


@pytest.mark.anyio
async def test_reject_out_of_range_values(transport):
    """Percentages above 100 should fail validation."""
    bad = {**VALID_INPUTS, "eligible_pct": 150.0}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/inputs", json=bad)

    assert resp.status_code == 422
