"""End-to-end integration tests for the HEOR Engine API.

Tests the full workflow: submit inputs -> save -> calculate BIA -> verify
response structure.  Also covers error handling (422, 404, malformed data).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


# ── Realistic device fixture ───────────────────────────────────────────
# AI-powered wound-assessment camera used in community nursing.
# Reduces Band 5 nurse time per dressing change and cuts follow-up visits.

WOUND_CAMERA_INPUTS = {
    # Section 1: Setting & Scope
    "setting": "Acute NHS Trust",
    "model_year": 2026,
    "forecast_years": 3,
    "funding_source": "Trust operational budget",
    # Section 2: Target Population
    "catchment_type": "population",
    "catchment_size": 350000,
    "eligible_pct": 2.5,           # chronic wound patients
    "uptake_y1": 15,
    "uptake_y2": 40,
    "uptake_y3": 65,
    "prevalence": "Chronic wound prevalence ~2.5% of catchment; rising with ageing population",
    # Section 3: Current Pathway
    "workforce": [
        {"role": "Band 5 (Staff Nurse)", "minutes": 45, "frequency": "per patient"},
        {"role": "Band 6 (Senior Nurse/AHP)", "minutes": 15, "frequency": "per patient"},
        {"role": "Consultant", "minutes": 10, "frequency": "per patient"},
        {"role": "Admin/Clerical", "minutes": 5, "frequency": "per patient"},
    ],
    "outpatient_visits": 6,
    "tests": 3,
    "admissions": 1,
    "bed_days": 4,
    "procedures": 0,
    "consumables": 85.0,
    # Section 4: Intervention & Pricing
    "pricing_model": "per-patient",
    "price": 750.0,
    "price_unit": "per year",
    "needs_training": True,
    "training_roles": "Band 5 nurses, Band 6 senior nurses",
    "training_hours": 3.0,
    "setup_cost": 8000.0,
    # Resource changes
    "staff_time_saved": 20.0,      # 20 mins saved per dressing assessment
    "visits_reduced": 25.0,        # fewer outpatient follow-ups needed
    "complications_reduced": 15.0, # earlier detection of wound deterioration
    "readmissions_reduced": 10.0,  # fewer emergency re-admissions
    "los_reduced": 0.5,            # half a day shorter stay
    "follow_up_reduced": 30.0,     # AI triage reduces unnecessary reviews
    # Comparator & Discounting
    "comparator": "none",
    "comparator_names": "Visual assessment, paper wound chart",
    "discounting": "off",
}


@pytest.fixture
def transport():
    return ASGITransport(app=app)


# ====================================================================
# 1. Full workflow integration
# ====================================================================

class TestFullWorkflow:
    """Submit -> Save -> Calculate -> Verify the complete response."""

    @pytest.mark.anyio
    async def test_end_to_end(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # ── Step 1: save inputs ────────────────────────────────────
            save_resp = await c.post("/api/inputs", json=WOUND_CAMERA_INPUTS)
            assert save_resp.status_code == 200, (
                f"Save failed: {save_resp.text}"
            )
            save_body = save_resp.json()
            assert "id" in save_body, "Response must contain 'id'"
            assert save_body["status"] == "saved"
            assert save_body["workforce_cost_per_patient"] > 0, (
                "Workforce cost should be positive"
            )
            submission_id = save_body["id"]

            # ── Step 2: calculate BIA ──────────────────────────────────
            calc_resp = await c.post(
                "/api/calculate-bia",
                params={"submission_id": submission_id},
            )
            assert calc_resp.status_code == 200, (
                f"Calculate failed: {calc_resp.text}"
            )
            body = calc_resp.json()

            # ── Step 3: verify top-level keys ──────────────────────────
            for key in ("submission_id", "validation", "summary",
                        "base", "conservative", "optimistic"):
                assert key in body, f"Missing top-level key '{key}'"

            # ── Step 4: verify validation block ────────────────────────
            v = body["validation"]
            assert isinstance(v["warnings"], list), "warnings must be a list"
            assert isinstance(v["suggestions"], list), "suggestions must be a list"
            assert v["confidence"] in ("High", "Medium", "Low"), (
                f"Unexpected confidence value: {v['confidence']}"
            )

            # ── Step 5: verify summary ─────────────────────────────────
            s = body["summary"]
            assert s["eligible_patients"] > 0, "Should have eligible patients"
            assert len(s["treated_patients"]) == 3, "Need 3 years of treated patients"

            # ── Step 6: verify scenario structure ──────────────────────
            for scenario_name in ("base", "conservative", "optimistic"):
                sc = body[scenario_name]
                assert "annual_budget_impact" in sc, (
                    f"{scenario_name}: missing annual_budget_impact"
                )
                assert "cost_per_patient" in sc, (
                    f"{scenario_name}: missing cost_per_patient"
                )
                assert "total_treated_patients" in sc, (
                    f"{scenario_name}: missing total_treated_patients"
                )
                assert "break_even_year" in sc, (
                    f"{scenario_name}: missing break_even_year"
                )
                assert "top_cost_drivers" in sc, (
                    f"{scenario_name}: missing top_cost_drivers"
                )
                assert len(sc["annual_budget_impact"]) == 3, (
                    f"{scenario_name}: expected 3 years of impact"
                )
                assert len(sc["cost_per_patient"]) == 3, (
                    f"{scenario_name}: expected 3 years of cpp"
                )

    @pytest.mark.anyio
    async def test_scenario_ordering(self, transport):
        """Conservative impact should be less negative (= worse) than base,
        and base less negative than optimistic, for a cost-saving device."""
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            save_resp = await c.post("/api/inputs", json=WOUND_CAMERA_INPUTS)
            sid = save_resp.json()["id"]

            calc_resp = await c.post(
                "/api/calculate-bia",
                params={"submission_id": sid},
            )
            body = calc_resp.json()

            cons = body["conservative"]["annual_budget_impact"]
            base = body["base"]["annual_budget_impact"]
            opt = body["optimistic"]["annual_budget_impact"]

            for yr in range(3):
                assert opt[yr] <= base[yr], (
                    f"Year {yr+1}: optimistic (£{opt[yr]:,.2f}) should be "
                    f"<= base (£{base[yr]:,.2f})"
                )
                assert base[yr] <= cons[yr], (
                    f"Year {yr+1}: base (£{base[yr]:,.2f}) should be "
                    f"<= conservative (£{cons[yr]:,.2f})"
                )

    @pytest.mark.anyio
    async def test_submissions_list_includes_new_entry(self, transport):
        """After saving, the submission should appear in GET /api/submissions."""
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            save_resp = await c.post("/api/inputs", json=WOUND_CAMERA_INPUTS)
            new_id = save_resp.json()["id"]

            list_resp = await c.get("/api/submissions")
            assert list_resp.status_code == 200
            ids = [s["id"] for s in list_resp.json()["submissions"]]
            assert new_id in ids, (
                f"New submission '{new_id}' not found in submissions list"
            )


# ====================================================================
# 2. Error handling
# ====================================================================

class TestErrorHandling:

    @pytest.mark.anyio
    async def test_invalid_json_returns_422(self, transport):
        """Completely invalid JSON body should return 422."""
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/inputs",
                content="this is not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 422, (
            f"Expected 422 for invalid JSON, got {resp.status_code}"
        )

    @pytest.mark.anyio
    async def test_missing_required_fields_returns_422(self, transport):
        """A partial payload missing required fields should return 422."""
        partial = {
            "setting": "Acute NHS Trust",
            "model_year": 2026,
            # missing: forecast_years, funding_source, catchment_size,
            #          eligible_pct, uptake_y1/y2/y3, workforce, price
        }
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/inputs", json=partial)

        assert resp.status_code == 422, (
            f"Expected 422 for missing required fields, got {resp.status_code}"
        )
        errors = resp.json()["detail"]
        missing_fields = {e["loc"][-1] for e in errors}
        assert "forecast_years" in missing_fields, "Should flag forecast_years"
        assert "funding_source" in missing_fields, "Should flag funding_source"
        assert "workforce" in missing_fields, "Should flag workforce"
        assert "price" in missing_fields, "Should flag price"

    @pytest.mark.anyio
    async def test_nonexistent_submission_returns_404(self, transport):
        """Calculating BIA for a non-existent ID should return 404."""
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/calculate-bia",
                params={"submission_id": "does_not_exist_99999"},
            )
        assert resp.status_code == 404, (
            f"Expected 404 for non-existent submission, got {resp.status_code}"
        )
        assert "not found" in resp.json()["detail"].lower(), (
            "404 detail should mention 'not found'"
        )

    @pytest.mark.anyio
    async def test_wrong_enum_returns_422(self, transport):
        """An invalid enum value should be rejected with 422."""
        bad = {**WOUND_CAMERA_INPUTS, "setting": "Private Hospital"}
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/inputs", json=bad)

        assert resp.status_code == 422, (
            f"Expected 422 for invalid enum, got {resp.status_code}"
        )

    @pytest.mark.anyio
    async def test_out_of_range_returns_422(self, transport):
        """Values violating field constraints should be rejected."""
        bad = {**WOUND_CAMERA_INPUTS, "eligible_pct": 200.0}
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/inputs", json=bad)

        assert resp.status_code == 422, (
            f"Expected 422 for eligible_pct=200, got {resp.status_code}"
        )

    @pytest.mark.anyio
    async def test_empty_body_returns_422(self, transport):
        """An empty JSON object should return 422."""
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/inputs", json={})

        assert resp.status_code == 422, (
            f"Expected 422 for empty body, got {resp.status_code}"
        )
