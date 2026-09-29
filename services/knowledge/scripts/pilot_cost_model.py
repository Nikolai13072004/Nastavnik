"""Transparent pilot unit-economics calculator.

The calculator deliberately has no built-in provider prices: pass actual VPS,
LLM and support costs from invoices or a staging run. This prevents a changing
tariff or an optimistic token estimate from silently becoming a sales price.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PilotCosts:
    currency: str
    pilot_price: float
    active_users: int
    completed_requests: int
    infrastructure: float
    llm_provider: float
    support_hours: float
    support_hourly_rate: float
    other_recurring: float = 0
    setup_hours: float = 0
    setup_hourly_rate: float = 0
    other_one_time: float = 0

    @classmethod
    def from_dict(cls, raw: dict) -> "PilotCosts":
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("unknown fields: " + ", ".join(sorted(unknown)))
        model = cls(**raw)
        if not model.currency.strip():
            raise ValueError("currency is required")
        if model.active_users <= 0 or model.completed_requests <= 0:
            raise ValueError("active_users and completed_requests must be positive")
        numeric = {name: getattr(model, name) for name in allowed - {"currency", "active_users", "completed_requests"}}
        if any(value < 0 for value in numeric.values()):
            raise ValueError("costs, hours and price cannot be negative")
        return model


def calculate(model: PilotCosts) -> dict:
    support = model.support_hours * model.support_hourly_rate
    recurring = model.infrastructure + model.llm_provider + support + model.other_recurring
    one_time = model.setup_hours * model.setup_hourly_rate + model.other_one_time
    contribution = model.pilot_price - recurring
    return {
        "currency": model.currency,
        "pilot_price": model.pilot_price,
        "active_users": model.active_users,
        "completed_requests": model.completed_requests,
        "recurring_cost": recurring,
        "one_time_delivery_cost": one_time,
        "first_pilot_total_cost": recurring + one_time,
        "recurring_cost_per_active_user": recurring / model.active_users,
        "recurring_cost_per_completed_request": recurring / model.completed_requests,
        "contribution_after_recurring_cost": contribution,
        "contribution_margin_percent": (contribution / model.pilot_price * 100) if model.pilot_price else None,
        "first_pilot_profit_after_all_listed_costs": model.pilot_price - recurring - one_time,
        "inputs_are_user_supplied": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate pilot economics from actual supplied costs")
    parser.add_argument("input", type=Path, help="JSON file with PilotCosts fields")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    report = calculate(PilotCosts.from_dict(json.loads(args.input.read_text(encoding="utf-8"))))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
