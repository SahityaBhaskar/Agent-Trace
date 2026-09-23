from typing import List
from ..models.report import AttentionItem, LogicalChange

class RiskEngine:
    """Risk & Attention Engine: Filters blast radius and flags high-leverage review concerns."""

    def evaluate_changes(self, changes: List[LogicalChange]) -> List[AttentionItem]:
        items: List[AttentionItem] = []

        for change in changes:
            # Check for financial or payment mutations
            services_str = " ".join(change.affected_services).lower()
            if "payment" in services_str or "charge" in services_str or "refund" in services_str:
                items.append(AttentionItem(
                    title="High Risk: Double-Debit Danger on Payment Retries",
                    level="HIGH",
                    detail=f"The agent modified {', '.join(change.affected_services)} without verifying external idempotency token support.",
                    action_required="Ensure each payment mutation passes a cryptographically unique `Idempotency-Key` header.",
                    jev_attention_probability=0.91
                ))

            # Check for recursive interceptor or token loops
            if "auth" in services_str or "token" in services_str or "interceptor" in services_str:
                items.append(AttentionItem(
                    title="Critical Risk: Infinite Loop on Refresh 401",
                    level="HIGH",
                    detail="If the refresh token itself expires, the interceptor must terminate with logout instead of re-triggering refresh.",
                    action_required="Verify originalRequest.url check avoids recursive /auth/refresh calls.",
                    jev_attention_probability=0.97
                ))

            # Check for broad blast radius (> 2 services)
            if len(change.affected_services) >= 3:
                items.append(AttentionItem(
                    title="Medium Risk: Cross-Service Blast Radius Coupling",
                    level="MEDIUM",
                    detail=f"3 separate services ({', '.join(change.affected_services)}) are now coupled to a shared utility.",
                    action_required="Run full integration test suite across all 3 dependent services before merging.",
                    jev_attention_probability=0.82
                ))

        return items

risk_engine = RiskEngine()
