#!/usr/bin/env python3
"""Read-only rehearsal input validation, NOT a chain/service/booth acceptance test."""

import json
from decimal import Decimal
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "docs/booth/scenarios.json").read_text())
    checks = 0

    def require(condition, message):
        nonlocal checks
        if not condition:
            raise ValueError(message)
        checks += 1

    def atoms(value):
        scaled = Decimal(value) * 1000000
        require(scaled == scaled.to_integral_value(), "more than six decimal places")
        return int(scaled)

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key.endswith("_atomic"):
                    require(isinstance(value, str) and value.isdigit(), f"integer string required: {key}")
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
        else:
            require(not isinstance(obj, float), "float in fixture")

    walk(data)
    require(data["preparation_status"] == "tabletop_only_not_end_to_end_passed", "false readiness claim")
    require(len(data["cases"]) == 2, "two cases required")
    require(len({case["case_key"] for case in data["cases"]}) == 2, "independent case IDs")

    for case in data["cases"]:
        po, invoice, receipt = case["purchase_order"], case["invoice"], case["goods_and_receipt"]
        total = sum(int(d["amount_atomic"]) for d in case["donations"])
        reserve, cost = int(po["reserve_atomic"]), int(invoice["invoice_amount_atomic"])
        require(0 < cost <= reserve <= int(po["budget_cap_atomic"]) <= total, "cap/reserve/custody")
        require(po["quantity"] == invoice["quantity"] == receipt["delivered_quantity"], "unplanned partial delivery")
        require(atoms(po["unit_price_hkd"]) * po["quantity"] == int(po["gross_atomic"]), "PO amount")
        require(atoms(invoice["unit_price_hkd"]) * invoice["quantity"] == int(invoice["gross_atomic"]), "invoice gross")
        require(int(invoice["gross_atomic"]) - int(invoice["discount_atomic"]) == cost, "invoice discount")
        require(invoice["discount_atomic"] == po["agreed_invoice_discount_atomic"], "discount not agreed")
        require(invoice["vendor_label"] == po["vendor_label"] and invoice["po_label"] == po["document_label"], "document binding")
        refund = case["expected_refund"]
        pool = int(refund["pool_atomic"])
        require(pool == total - cost, "refund pool")
        prefix = 0
        entitlements = []
        for donation in sorted(case["donations"], key=lambda d: d["order"]):
            end = prefix + int(donation["amount_atomic"])
            entitlement = pool * end // total - pool * prefix // total
            require(entitlement == int(refund[f"{donation['role']}_atomic"]), "interval refund entitlement")
            require(atoms(donation["exchange_hkd"]) == int(donation["amount_atomic"]), "mock 1:1 donation")
            entitlements.append(entitlement)
            prefix = end
        require(sum(entitlements) == pool, "refund conservation")
        payment = case["expected_payment"]
        require(atoms(payment["foundation_mock_hkd_credit"]) == cost == atoms(payment["fixed_vendor_mock_hkd_credit"]), "payment ledger amounts")
        require(int(payment["mock_redemption_atomic"]) == cost, "redemption amount")
        if case["case_key"] == "A_live":
            pre, post = case["expected_after_reserve"], case["expected_after_release"]
            require(int(pre["free_locked_atomic"]) == total - reserve, "pre freeLocked")
            require(int(post["reserved_atomic"]) == 0, "reservation removed")
            require(int(post["project_custody_liability_atomic"]) == pool, "post custody")
            require(int(refund["remaining_after_a_claim_atomic"]) == int(refund["donor_b_atomic"]), "unclaimed B")
            require(refund["state_after_a_claim_only"] == "Refundable", "not closed early")
            require(int(case["blocked_tests"][0]["attempt_invoice_atomic"]) > reserve, "overspend failure")
            require(case["booth_start"]["deposits_atomic"] == case["donations"][0]["amount_atomic"], "preload B")

    print(f"PASS: {checks} fixture checks; two cases use donor-order integer allocation and consistent documents/payment/refunds.")
    print("Preparation data only. No chain transactions or service calls executed; NOT an end-to-end booth pass.")


if __name__ == "__main__":
    main()
