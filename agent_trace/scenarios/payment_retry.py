from ..models.session import SessionEvent
from ..models.jev_types import JevEvaluationResult, JevClassificationResult, JevBlastRadiusResult, JevHumanReviewResult, JevBreakingRiskResult
from ..models.graph import CausalNode, CausalEdge, CausalGraphData
from ..models.report import (
    ScenarioData,
    SessionStats,
    LogicalChange,
    DiffHunk,
    ReviewChecklistItem,
    ExecutionFlowDiff,
    FlowDescription,
    ExecutionFlowStep,
    GroundedConcept,
    AttentionItem,
)

def get_payment_retry_scenario() -> ScenarioData:
    events = [
        SessionEvent(
            event_id="evt_01",
            session_id="ses_payment_retry",
            sequence_number=1,
            timestamp="14:00:01",
            agent_id="claude-code",
            action_type="SESSION_STARTED",
            epistemic_status="OBSERVED",
            payload={"output_summary": "Session initialized in /workspace/fintech-core"}
        ),
        SessionEvent(
            event_id="evt_02",
            session_id="ses_payment_retry",
            sequence_number=2,
            timestamp="14:00:02",
            agent_id="claude-code",
            action_type="USER_REQUEST",
            epistemic_status="DECLARED",
            target={"query": "Add retry handling to payment requests."},
            payload={"output_summary": "User requested retry logic for PaymentService"}
        ),
        SessionEvent(
            event_id="evt_03",
            session_id="ses_payment_retry",
            sequence_number=3,
            timestamp="14:00:05",
            agent_id="claude-code",
            action_type="FILE_READ",
            epistemic_status="OBSERVED",
            target={"file_path": "src/services/PaymentService.ts"},
            payload={"output_summary": "Inspected payment charge handler and HTTP dispatch"}
        ),
        SessionEvent(
            event_id="evt_04",
            session_id="ses_payment_retry",
            sequence_number=4,
            timestamp="14:00:09",
            agent_id="claude-code",
            action_type="SYMBOL_SEARCH",
            epistemic_status="OBSERVED",
            target={"symbol_name": "retryPayment"},
            payload={"match_count": 1, "output_summary": "Found incomplete stub in PaymentService"}
        ),
        SessionEvent(
            event_id="evt_05",
            session_id="ses_payment_retry",
            sequence_number=5,
            timestamp="14:00:14",
            agent_id="claude-code",
            action_type="REPOSITORY_SEARCH",
            epistemic_status="OBSERVED",
            target={"query": "attempts < "},
            payload={"match_count": 3, "output_summary": "Found 3 identical retry loops in PaymentService.ts, RefundService.ts, and SubscriptionService.ts"}
        ),
        SessionEvent(
            event_id="evt_06",
            session_id="ses_payment_retry",
            sequence_number=6,
            timestamp="14:00:18",
            agent_id="claude-code",
            action_type="FILE_READ",
            epistemic_status="OBSERVED",
            target={"file_path": "src/services/RefundService.ts"},
            payload={"output_summary": "Found custom while loop with linear 1000ms delay"}
        ),
        SessionEvent(
            event_id="evt_07",
            session_id="ses_payment_retry",
            sequence_number=7,
            timestamp="14:00:22",
            agent_id="claude-code",
            action_type="FILE_READ",
            epistemic_status="OBSERVED",
            target={"file_path": "src/services/SubscriptionService.ts"},
            payload={"output_summary": "Found recursive retry renewal handler with fixed 2000ms backoff"}
        ),
        SessionEvent(
            event_id="evt_08",
            session_id="ses_payment_retry",
            sequence_number=8,
            timestamp="14:00:27",
            agent_id="claude-code",
            action_type="AGENT_REASONING_NOTE",
            epistemic_status="DECLARED",
            payload={"output_summary": "Identified 3 duplicated retry patterns across services. Proposing centralized RetryExecutor to avoid adding a fourth ad-hoc implementation."}
        ),
        SessionEvent(
            event_id="evt_09",
            session_id="ses_payment_retry",
            sequence_number=9,
            timestamp="14:00:30",
            agent_id="claude-code",
            action_type="FILE_CREATED",
            epistemic_status="OBSERVED",
            target={"file_path": "src/utils/RetryExecutor.ts"},
            payload={"output_summary": "Created generic RetryExecutor supporting exponential backoff with jitter"}
        ),
        SessionEvent(
            event_id="evt_10",
            session_id="ses_payment_retry",
            sequence_number=10,
            timestamp="14:00:34",
            agent_id="claude-code",
            action_type="FILE_MODIFIED",
            epistemic_status="OBSERVED",
            target={"file_path": "src/services/PaymentService.ts", "symbol_name": "charge"},
            payload={"output_summary": "Migrated charge() to use RetryExecutor.execute()"}
        ),
        SessionEvent(
            event_id="evt_11",
            session_id="ses_payment_retry",
            sequence_number=11,
            timestamp="14:00:37",
            agent_id="claude-code",
            action_type="FILE_MODIFIED",
            epistemic_status="OBSERVED",
            target={"file_path": "src/services/RefundService.ts", "symbol_name": "processRefund"},
            payload={"output_summary": "Replaced manual while loop with RetryExecutor"}
        ),
        SessionEvent(
            event_id="evt_12",
            session_id="ses_payment_retry",
            sequence_number=12,
            timestamp="14:00:40",
            agent_id="claude-code",
            action_type="FILE_MODIFIED",
            epistemic_status="OBSERVED",
            target={"file_path": "src/services/SubscriptionService.ts", "symbol_name": "renewSubscription"},
            payload={"output_summary": "Standardized subscription renewal on RetryExecutor"}
        ),
        SessionEvent(
            event_id="evt_13",
            session_id="ses_payment_retry",
            sequence_number=13,
            timestamp="14:00:44",
            agent_id="claude-code",
            action_type="FILE_CREATED",
            epistemic_status="OBSERVED",
            target={"file_path": "src/tests/RetryExecutor.test.ts"},
            payload={"output_summary": "Added 4 unit tests covering jitter, max retries, and network errors"}
        ),
        SessionEvent(
            event_id="evt_14",
            session_id="ses_payment_retry",
            sequence_number=14,
            timestamp="14:00:48",
            agent_id="claude-code",
            action_type="TEST_EXECUTED",
            epistemic_status="OBSERVED",
            target={"command": "npm test -- RetryExecutor"},
            payload={"exit_code": 0, "output_summary": "PASS (6/6 tests passing in 420ms)"}
        ),
        SessionEvent(
            event_id="evt_15",
            session_id="ses_payment_retry",
            sequence_number=15,
            timestamp="14:00:50",
            agent_id="claude-code",
            action_type="SESSION_COMPLETED",
            epistemic_status="OBSERVED",
            payload={"output_summary": "Git diff prepared (8 files, +142 -88 lines)"}
        ),
    ]

    hunks = [
        DiffHunk(
            id="hunk_payment_1",
            file_path="src/services/PaymentService.ts",
            symbol="PaymentService.charge",
            line_range="Lines 42–59",
            change_type="MODIFIED",
            old_lines="""  async charge(amount: number, cardToken: string) {
    let attempts = 0;
    while (attempts < 3) {
      try {
        return await this.http.post('/v1/charges', { amount, cardToken });
      } catch (err) {
        attempts++;
        if (attempts >= 3) throw err;
        await sleep(1000);
      }
    }
  }""",
            new_lines="""  async charge(amount: number, cardToken: string, idempotencyKey: string) {
    return await this.retryExecutor.execute(
      () => this.http.post('/v1/charges', { amount, cardToken }, {
        headers: { 'Idempotency-Key': idempotencyKey }
      }),
      { maxRetries: 3, backoff: 'exponential' }
    );
  }""",
            jev_result=JevEvaluationResult(
                hunk_id="hunk_payment_1",
                classification=JevClassificationResult(
                    category="COMMONIZATION",
                    confidence=0.96,
                    choice="COMMONIZATION",
                ),
                blast_radius=JevBlastRadiusResult(
                    score=3.4,
                    level="service_level",
                ),
                human_review=JevHumanReviewResult(
                    required=True,
                    probability=0.91,
                ),
                breaking_risk=JevBreakingRiskResult(
                    is_breaking=True,
                    probability=0.72,
                ),
                latency_ms=112,
                source="calibrated_cache",
            ),
            evidence_event_ids=["evt_03", "evt_05", "evt_09", "evt_10"],
        ),
        DiffHunk(
            id="hunk_refund_1",
            file_path="src/services/RefundService.ts",
            symbol="RefundService.processRefund",
            line_range="Lines 31–47",
            change_type="MODIFIED",
            old_lines="""  async processRefund(chargeId: string, amount: number) {
    for (let i = 0; i < 2; i++) {
      try {
        return await this.gateway.refund({ chargeId, amount });
      } catch (e) {
        if (i === 1) throw new RefundFailedError(e);
        await wait(500 * (i + 1));
      }
    }
  }""",
            new_lines="""  async processRefund(chargeId: string, amount: number) {
    return await this.retryExecutor.execute(
      () => this.gateway.refund({ chargeId, amount }),
      { maxRetries: 2, backoff: 'exponential' }
    );
  }""",
            jev_result=JevEvaluationResult(
                hunk_id="hunk_refund_1",
                classification=JevClassificationResult(
                    category="COMMONIZATION",
                    confidence=0.94,
                    choice="COMMONIZATION",
                ),
                blast_radius=JevBlastRadiusResult(
                    score=3.1,
                    level="service_level",
                ),
                human_review=JevHumanReviewResult(
                    required=True,
                    probability=0.88,
                ),
                breaking_risk=JevBreakingRiskResult(
                    is_breaking=False,
                    probability=0.14,
                ),
                latency_ms=98,
                source="calibrated_cache",
            ),
            evidence_event_ids=["evt_05", "evt_06", "evt_11"],
        ),
        DiffHunk(
            id="hunk_retry_new",
            file_path="src/utils/RetryExecutor.ts",
            symbol="class RetryExecutor",
            line_range="Lines 1–48 (New File)",
            change_type="ADDED",
            old_lines="// File did not exist prior to agent session",
            new_lines="""export interface RetryOptions {
  maxRetries: number;
  backoff: 'linear' | 'exponential';
  initialDelayMs?: number;
}

export class RetryExecutor {
  async execute<T>(fn: () => Promise<T>, opts: RetryOptions): Promise<T> {
    let attempt = 0;
    const initial = opts.initialDelayMs ?? 1000;
    while (true) {
      try {
        return await fn();
      } catch (err: any) {
        attempt++;
        if (attempt >= opts.maxRetries || err.status < 500) {
          throw err;
        }
        const delay = opts.backoff === 'exponential'
          ? initial * Math.pow(2, attempt - 1) + Math.random() * 200
          : initial * attempt;
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }
}""",
            jev_result=JevEvaluationResult(
                hunk_id="hunk_retry_new",
                classification=JevClassificationResult(
                    category="COMMONIZATION",
                    confidence=0.98,
                    choice="COMMONIZATION",
                ),
                blast_radius=JevBlastRadiusResult(
                    score=3.8,
                    level="service_level",
                ),
                human_review=JevHumanReviewResult(
                    required=True,
                    probability=0.94,
                ),
                breaking_risk=JevBreakingRiskResult(
                    is_breaking=False,
                    probability=0.08,
                ),
                latency_ms=125,
                source="calibrated_cache",
            ),
            evidence_event_ids=["evt_08", "evt_09", "evt_13", "evt_14"],
        ),
    ]

    changes = [
        LogicalChange(
            id="change_01",
            title="Introduction of RetryExecutor & Commonization across 3 Services",
            category="COMMONIZATION",
            why_explanation="The agent discovered three existing, fragmented retry loops while investigating how to implement payment retries. Instead of creating a fourth bespoke implementation, it consolidated shared backoff logic into RetryExecutor.",
            how_arrived_steps=[
                "User requested retry handling for Payment requests.",
                "Agent traced PaymentService.charge() call flow.",
                "Searched repo for existing retry implementations (grep 'attempts < ').",
                "Found identical ad-hoc loops in RefundService and SubscriptionService.",
                "Created src/utils/RetryExecutor.ts with exponential backoff & jitter.",
                "Migrated all three services to the shared executor.",
                "Added dedicated unit test suite for RetryExecutor.",
            ],
            epistemic_status="OBSERVED",
            confidence_score=0.96,
            hunks=hunks,
            affected_services=["PaymentService", "RefundService", "SubscriptionService"],
            blast_radius="service_level",
            review_checklist=[
                ReviewChecklistItem(
                    item="Verify Idempotency Keys on Payment & Refund Endpoints",
                    severity="CRITICAL",
                    rationale="Retrying POST /charges or /refunds across network dropouts can cause double-debiting if backend lacks idempotency keys.",
                ),
                ReviewChecklistItem(
                    item="Review Timeout Multiplier & Worker Blocking",
                    severity="WARNING",
                    rationale="Max 3 retries with 2s exponential backoff can hold request threads for up to 14 seconds during upstream gateway outages.",
                ),
                ReviewChecklistItem(
                    item="Inspect 4xx Client Error Handling",
                    severity="WARNING",
                    rationale="Ensure RetryExecutor only retries 5xx server errors and network dropouts, not 400 Bad Request or 401 Unauthorized.",
                ),
            ],
        )
    ]

    causal_nodes = [
        CausalNode(
            id="node_req",
            type="USER_REQUEST",
            title='User Prompt: "Add retry to payment"',
            subtitle="Initiated at 14:00:02",
            epistemic_status="DECLARED",
            confidence_score=1.0,
            data={"details": "Developer prompted agent to add retry handling to payment requests."},
        ),
        CausalNode(
            id="node_search",
            type="AGENT_INVESTIGATION",
            title='grep "attempts < "',
            subtitle="Found 3 identical loops in repo",
            epistemic_status="OBSERVED",
            confidence_score=0.98,
            data={
                "evidenceEvents": ["evt_05", "evt_06", "evt_07"],
                "details": "Agent discovered that RefundService and SubscriptionService were duplicating retry logic.",
            },
        ),
        CausalNode(
            id="node_dec",
            type="ARCHITECTURAL_DECISION",
            title="Decision: Extract Shared RetryExecutor",
            subtitle="Prevent architectural drift & duplication",
            epistemic_status="OBSERVED",
            confidence_score=0.95,
            data={"details": "Rather than adding a 4th ad-hoc retry loop, agent consolidated behavior into a reusable utility."},
        ),
        CausalNode(
            id="node_hunk_util",
            type="LOGICAL_CHANGE",
            title="RetryExecutor.ts",
            subtitle="src/utils · 1 hunk (NEW)",
            epistemic_status="OBSERVED",
            confidence_score=0.98,
            data={"filePath": "src/utils/RetryExecutor.ts", "file_path": "src/utils/RetryExecutor.ts", "jevCategory": "COMMONIZATION", "jevRiskScore": 3.8, "hunk_count": 1, "symbols": ["RetryExecutor"]},
        ),
        CausalNode(
            id="node_hunk_pay",
            type="CODE_HUNK",
            title="PaymentService.ts",
            subtitle="src/services · charge() redirected (L42–59)",
            epistemic_status="OBSERVED",
            confidence_score=0.96,
            data={"filePath": "src/services/PaymentService.ts", "file_path": "src/services/PaymentService.ts", "lineRange": "42-59", "line_range": "42-59", "jevCategory": "COMMONIZATION", "hunk_count": 1, "symbols": ["charge"]},
        ),
        CausalNode(
            id="node_hunk_ref",
            type="CODE_HUNK",
            title="RefundService.ts",
            subtitle="src/services · processRefund() updated (L31–47)",
            epistemic_status="OBSERVED",
            confidence_score=0.94,
            data={"filePath": "src/services/RefundService.ts", "file_path": "src/services/RefundService.ts", "lineRange": "31-47", "line_range": "31-47", "hunk_count": 1, "symbols": ["processRefund"]},
        ),
        CausalNode(
            id="node_risk",
            type="RISK_FLAG",
            title="High Risk: Double-Debit Danger on Payment Retries",
            subtitle="Jev Attention Probability: 91%",
            epistemic_status="INFERRED",
            confidence_score=0.91,
            data={"risk_index": 0, "details": "Retrying charge() without idempotency headers risks duplicate client transactions upon network timeout."},
        ),
        CausalNode(
            id="node_learn",
            type="LEARNING_CONCEPT",
            title="The Resilient Retry Pattern",
            subtitle="Architectural concept detected in PR",
            epistemic_status="OBSERVED",
            confidence_score=0.99,
            data={"tags": ["Resilience", "Distributed Systems", "Exponential Backoff", "Idempotency"]},
        ),
    ]

    causal_edges = [
        CausalEdge(id="e1", source="node_req", target="node_search", relation="MOTIVATED_BY", confidence=0.99),
        CausalEdge(id="e2", source="node_search", target="node_dec", relation="DISCOVERED_IN", confidence=0.96),
        CausalEdge(id="e3", source="node_dec", target="node_hunk_util", relation="INTRODUCED", confidence=0.98),
        CausalEdge(id="e4", source="node_hunk_util", target="node_hunk_pay", relation="MODIFIES", confidence=0.95),
        CausalEdge(id="e5", source="node_hunk_util", target="node_hunk_ref", relation="MODIFIES", confidence=0.93),
        CausalEdge(id="e6", source="node_hunk_pay", target="node_risk", relation="CREATES_RISK", confidence=0.91),
        CausalEdge(id="e7", source="node_hunk_util", target="node_learn", relation="GROUNDED_IN", confidence=0.99),
    ]

    return ScenarioData(
        id="payment-retry",
        title="Scenario 1: Payment Retry Commonization (3 Services)",
        description="The agent extracted a shared RetryExecutor across Payment, Refund, and Subscription services after discovering duplicate retry loops.",
        user_prompt="Add retry handling to payment requests.",
        stats=SessionStats(
            files_inspected=18,
            functions_analyzed=31,
            relevant_paths=4,
            tests_run=6,
            execution_time_seconds=38,
        ),
        events=events,
        logical_changes=changes,
        flow_diff=ExecutionFlowDiff(
            before_flow=FlowDescription(
                description="Fragmented, ad-hoc retry loops inside each service with inconsistent backoffs and no jitter.",
                steps=[
                    ExecutionFlowStep(name="PaymentService", role="Charges checkout", type="service"),
                    ExecutionFlowStep(name="Ad-hoc While Loop", role="In-memory retries (1s linear)", type="utility"),
                    ExecutionFlowStep(name="HTTP Client", role="Axios instance", type="client"),
                    ExecutionFlowStep(name="Stripe Gateway", role="External API", type="external"),
                ],
            ),
            after_flow=FlowDescription(
                description="Unified resilience architecture: All services delegate to centralized RetryExecutor with exponential backoff, jitter, and idempotency protection.",
                steps=[
                    ExecutionFlowStep(name="Payment / Refund / Sub", role="3 business flows", type="service"),
                    ExecutionFlowStep(name="RetryExecutor", role="Centralized policy + Jitter", type="utility"),
                    ExecutionFlowStep(name="HTTP Client", role="Idempotency Header injected", type="client"),
                    ExecutionFlowStep(name="Stripe Gateway", role="Protected against duplicate charges", type="external"),
                ],
            ),
            mermaid_diagram="""flowchart LR
    subgraph After [Unified Resilience Architecture]
      PS[PaymentService] --> RE[RetryExecutor]
      RS[RefundService] --> RE
      SS[SubscriptionService] --> RE
      RE --> HTTP[HTTP Client + Idempotency]
      HTTP --> GATEWAY[Payment Gateway API]
    end""",
        ),
        causal_graph=CausalGraphData(nodes=causal_nodes, edges=causal_edges),
        grounded_concept=GroundedConcept(
            name="The Resilient Retry Pattern",
            headline="Exponential Backoff with Full Jitter and Idempotency Guarding",
            what_it_is="A foundational distributed systems pattern where failed transient network operations are retried with exponentially increasing intervals and random jitter to avoid thundering herd failures.",
            how_your_repo_uses_it="Instead of having each microservice implement its own loop, your agent extracted RetryExecutor.ts. In PaymentService.ts line 43, it now wraps HTTP requests with exponential backoff and injects an Idempotency-Key header.",
            code_snippet="""// Grounded in your newly added src/utils/RetryExecutor.ts
await this.retryExecutor.execute(
  () => this.http.post('/v1/charges', payload, {
    headers: { 'Idempotency-Key': idempotencyKey }
  }),
  { maxRetries: 3, backoff: 'exponential' }
);""",
            pitfalls_to_watch=[
                "Retrying 4xx client errors (e.g. 400 Bad Request, 401 Unauthorized) — only transient 5xx server errors or socket timeouts should be retried.",
                "Missing Idempotency Keys on retried financial mutations (can lead to double-charging).",
                "Thread/Worker starvation when compounding retries multiply total connection wait time.",
            ],
            related_concepts=["Circuit Breaker Pattern", "Idempotent Consumer", "Dead Letter Queue (DLQ)", "Rate Limiting"],
        ),
        attention_items=[
            AttentionItem(
                title="High Risk: Double-Debit Danger on Payment Retries",
                level="HIGH",
                detail="RetryExecutor was wired into PaymentService.charge() without verifying that the external gateway supports automatic deduplication.",
                action_required="Ensure each payment request generates and passes a cryptographically unique `Idempotency-Key` header.",
                jev_attention_probability=0.91,
            ),
            AttentionItem(
                title="Medium Risk: Cascade Timeout on Gateway Outage",
                level="MEDIUM",
                detail="With 3 retries and exponential backoff, worker threads can hang for up to 14 seconds before failing back to user.",
                action_required="Add a hard 5000ms global timeout across all retry attempts combined.",
                jev_attention_probability=0.78,
            ),
            AttentionItem(
                title="Low Risk: Untested Subscription Edge Case",
                level="LOW",
                detail="SubscriptionService.renewSubscription() was migrated to RetryExecutor, but tests only cover PaymentService.",
                action_required="Add integration test verifying subscription renewal recovery on 503 Service Unavailable.",
                jev_attention_probability=0.42,
            ),
        ],
    )
