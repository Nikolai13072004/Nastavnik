import pytest

from scripts.pilot_cost_model import PilotCosts, calculate


def test_cost_model_separates_recurring_and_one_time_costs():
    report = calculate(PilotCosts(
        currency="RUB", pilot_price=100_000, active_users=25, completed_requests=500,
        infrastructure=10_000, llm_provider=5_000,
        support_hours=10, support_hourly_rate=1_000,
        other_recurring=2_000, setup_hours=8, setup_hourly_rate=2_000,
        other_one_time=4_000,
    ))
    assert report["recurring_cost"] == 27_000
    assert report["one_time_delivery_cost"] == 20_000
    assert report["recurring_cost_per_active_user"] == 1_080
    assert report["recurring_cost_per_completed_request"] == 54
    assert report["contribution_margin_percent"] == 73
    assert report["first_pilot_profit_after_all_listed_costs"] == 53_000


@pytest.mark.parametrize("field,value", [
    ("active_users", 0), ("completed_requests", 0), ("infrastructure", -1),
    ("pilot_price", -1), ("support_hours", -1),
])
def test_cost_model_rejects_invalid_inputs(field, value):
    raw = {
        "currency": "RUB", "pilot_price": 1, "active_users": 1,
        "completed_requests": 1, "infrastructure": 0, "llm_provider": 0,
        "support_hours": 0, "support_hourly_rate": 0,
    }
    raw[field] = value
    with pytest.raises(ValueError):
        PilotCosts.from_dict(raw)


def test_cost_model_rejects_unknown_fields_and_empty_currency():
    with pytest.raises(ValueError, match="unknown fields"):
        PilotCosts.from_dict({"currency": "RUB", "mystery": 1})
    with pytest.raises(ValueError, match="currency"):
        PilotCosts.from_dict({
            "currency": " ", "pilot_price": 0, "active_users": 1,
            "completed_requests": 1, "infrastructure": 0, "llm_provider": 0,
            "support_hours": 0, "support_hourly_rate": 0,
        })
