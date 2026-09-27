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

def get_auth_scenario() -> ScenarioData:
    events = [
        SessionEvent(
            event_id="evt_a01",
            session_id="ses_auth_interceptor",
            sequence_number=1,
            timestamp="15:10:02",
            agent_id="claude-code",
            action_type="SESSION_STARTED",
            epistemic_status="OBSERVED"
        ),
        SessionEvent(
            event_id="evt_a02",
            session_id="ses_auth_interceptor",
            sequence_number=2,
            timestamp="15:10:03",
            agent_id="claude-code",
            action_type="USER_REQUEST",
            epistemic_status="DECLARED",
            target={"query": "Handle expired JWT tokens automatically so users are not logged out randomly."}
        ),
        SessionEvent(
            event_id="evt_a03",
            session_id="ses_auth_interceptor",
            sequence_number=3,
            timestamp="15:10:09",
            agent_id="claude-code",
            action_type="REPOSITORY_SEARCH",
            epistemic_status="OBSERVED",
            target={"query": "catch (err) { if (err.status === 401)"},
            payload={"match_count": 7, "output_summary": "Found 7 separate components manually redirecting to /login on 401"}
        ),
        SessionEvent(
            event_id="evt_a04",
            session_id="ses_auth_interceptor",
            sequence_number=4,
            timestamp="15:10:18",
            agent_id="claude-code",
            action_type="FILE_CREATED",
            epistemic_status="OBSERVED",
            target={"file_path": "src/api/interceptors/tokenRefresh.ts"},
            payload={"output_summary": "Created Axios response interceptor with token queueing to avoid refresh stampedes"}
        ),
        SessionEvent(
            event_id="evt_a05",
            session_id="ses_auth_interceptor",
            sequence_number=5,
            timestamp="15:10:27",
            agent_id="claude-code",
            action_type="FILE_MODIFIED",
            epistemic_status="OBSERVED",
            target={"file_path": "src/api/httpClient.ts"},
            payload={"output_summary": "Registered tokenRefresh interceptor on base HTTP client"}
        ),
        SessionEvent(
            event_id="evt_a06",
            session_id="ses_auth_interceptor",
            sequence_number=6,
            timestamp="15:10:35",
            agent_id="claude-code",
            action_type="TEST_EXECUTED",
            epistemic_status="OBSERVED",
            target={"command": "npm test -- tokenRefresh"},
            payload={"exit_code": 0, "output_summary": "PASS: 8/8 tests passed"}
        ),
    ]

    hunk = DiffHunk(
        id="hunk_auth_1",
        file_path="src/api/interceptors/tokenRefresh.ts",
        symbol="attachTokenRefreshInterceptor",
        line_range="Lines 1–65 (New File)",
        change_type="ADDED",
        old_lines="// File did not exist prior to agent session",
        new_lines="""let isRefreshing = false;
let failedQueue: Array<{ resolve: (token: string) => void; reject: (err: any) => void }> = [];

export function attachTokenRefreshInterceptor(axiosInstance: AxiosInstance) {
  axiosInstance.interceptors.response.use(
    response => response,
    async error => {
      const originalRequest = error.config;
      if (error.response?.status === 401 && !originalRequest._retry) {
        if (originalRequest.url.includes('/auth/refresh')) {
          logoutUser();
          return Promise.reject(error);
        }
        originalRequest._retry = true;
        // Mutex queue logic...
      }
      return Promise.reject(error);
    }
  );
}""",
        jev_result=JevEvaluationResult(
            hunk_id="hunk_auth_1",
            classification=JevClassificationResult(
                category="SECURITY_RELEVANT_CHANGE",
                confidence=0.98,
                choice="SECURITY_RELEVANT_CHANGE",
            ),
            blast_radius=JevBlastRadiusResult(
                score=4.0,
                level="system_critical",
            ),
            human_review=JevHumanReviewResult(
                required=True,
                probability=0.97,
            ),
            breaking_risk=JevBreakingRiskResult(
                is_breaking=False,
                probability=0.22,
            ),
            latency_ms=104,
            source="calibrated_cache",
        ),
        evidence_event_ids=["evt_a03", "evt_a04", "evt_a05"],
    )

    change = LogicalChange(
        id="change_auth_1",
        title="Centralized JWT Refresh Interceptor with Concurrency Lock",
        category="SECURITY_RELEVANT_CHANGE",
        why_explanation="Rather than catching 401 errors manually in 7 different UI components, the agent moved token refresh into an HTTP interceptor with a mutex queue to prevent parallel refresh requests from invalidating tokens.",
        how_arrived_steps=[
            "User reported sudden user logouts on expired JWTs.",
            "Agent searched for 401 handling across codebase.",
            "Identified 7 components repeating manual token refreshes.",
            "Created src/api/interceptors/tokenRefresh.ts with pending request queue.",
            "Wired interceptor into global Axios client.",
            "Cleaned up redundant 401 boilerplate in UI components.",
        ],
        epistemic_status="OBSERVED",
        confidence_score=0.97,
        hunks=[hunk],
        affected_services=["HttpClient", "AuthService", "UserProfile", "BillingDashboard"],
        blast_radius="system_critical",
        review_checklist=[
            ReviewChecklistItem(
                item="Check Infinite Refresh Loop Safeguard",
                severity="CRITICAL",
                rationale="If /api/refresh itself returns a 401, the interceptor must not recursively call /api/refresh.",
            ),
            ReviewChecklistItem(
                item="Verify Token Queue Mutex on Concurrent Requests",
                severity="CRITICAL",
                rationale="If 5 parallel requests fail with 401 simultaneously, only ONE refresh request should fire while 4 wait in a promise queue.",
            ),
        ],
    )

    causal_nodes = [
        CausalNode(id="n_auth_req", type="USER_REQUEST", title='User Prompt: "Prevent random user logouts"', epistemic_status="DECLARED", confidence_score=1.0, data={"details": "Developer asked agent to prevent random session terminations."}),
        CausalNode(id="n_auth_search", type="AGENT_INVESTIGATION", title="Grep 401 status handlers", subtitle="Found 7 duplicate try/catch blocks", epistemic_status="OBSERVED", confidence_score=0.97, data={"details": "Discovered components were independently calling refresh endpoint."}),
        CausalNode(id="n_auth_dec", type="ARCHITECTURAL_DECISION", title="Decision: Implement Request Queue Interceptor", subtitle="Security & resilience pattern", epistemic_status="OBSERVED", confidence_score=0.98, data={"details": "Avoid token revocation race conditions."}),
        CausalNode(id="n_auth_hunk", type="CODE_HUNK", title="tokenRefresh.ts", subtitle="src/api/interceptors · 1 hunk (NEW)", epistemic_status="OBSERVED", confidence_score=0.98, data={"filePath": "src/api/interceptors/tokenRefresh.ts", "file_path": "src/api/interceptors/tokenRefresh.ts", "jevCategory": "SECURITY_RELEVANT_CHANGE", "hunk_count": 1, "symbols": ["AxiosInterceptor", "failedQueue"]}),
        CausalNode(id="n_auth_risk", type="RISK_FLAG", title="Critical Risk: Verify Recursive Refresh Loop Breakpoint", subtitle="Jev Attention: 97%", epistemic_status="INFERRED", confidence_score=0.97, data={"risk_index": 0, "details": "If refresh token itself is expired, loop must terminate immediately with logout."}),
        CausalNode(id="n_auth_learn", type="LEARNING_CONCEPT", title="The Interceptor & Mutex Queue Pattern", subtitle="Security pattern detected", epistemic_status="OBSERVED", confidence_score=0.99, data={"tags": ["Security", "JWT", "Mutex", "Concurrency", "HTTP Pipeline"]}),
    ]

    causal_edges = [
        CausalEdge(id="ea1", source="n_auth_req", target="n_auth_search", relation="MOTIVATED_BY", confidence=0.99),
        CausalEdge(id="ea2", source="n_auth_search", target="n_auth_dec", relation="DISCOVERED_IN", confidence=0.97),
        CausalEdge(id="ea3", source="n_auth_dec", target="n_auth_hunk", relation="INTRODUCED", confidence=0.98),
        CausalEdge(id="ea4", source="n_auth_hunk", target="n_auth_risk", relation="CREATES_RISK", confidence=0.97),
        CausalEdge(id="ea5", source="n_auth_hunk", target="n_auth_learn", relation="GROUNDED_IN", confidence=0.99),
    ]

    return ScenarioData(
        id="auth-interceptor",
        title="Scenario 2: Auth Token Refresh & Axios Interceptor Refactor",
        description="The agent migrated 12 direct API calls into an Axios HTTP Interceptor to handle automatic 401 JWT refresh and replay.",
        user_prompt="Handle expired JWT tokens automatically so users are not logged out randomly.",
        stats=SessionStats(
            files_inspected=24,
            functions_analyzed=45,
            relevant_paths=6,
            tests_run=8,
            execution_time_seconds=44,
        ),
        events=events,
        logical_changes=[change],
        flow_diff=ExecutionFlowDiff(
            before_flow=FlowDescription(
                description="Each component caught 401 separately, triggering multiple concurrent refresh calls and race conditions.",
                steps=[
                    ExecutionFlowStep(name="UI Components", role="7 separate callers", type="client"),
                    ExecutionFlowStep(name="Direct 401 Handler", role="Bespoke try/catch", type="utility"),
                    ExecutionFlowStep(name="Auth API", role="Spammed with 5 refresh tokens", type="external"),
                ],
            ),
            after_flow=FlowDescription(
                description="Single synchronized HTTP Interceptor with queueing locks and automatic request replay.",
                steps=[
                    ExecutionFlowStep(name="UI Components", role="Unaware of auth failures", type="client"),
                    ExecutionFlowStep(name="Axios Interceptor", role="Single mutex refresh + queue", type="utility"),
                    ExecutionFlowStep(name="Auth Server", role="Receives exactly 1 refresh call", type="external"),
                ],
            ),
            mermaid_diagram="""flowchart LR
    UI[UI Views] --> HTTP[HttpClient]
    HTTP --> INT[Token Refresh Interceptor]
    INT -->|If 401| QUEUE[Mutex Promise Queue]
    QUEUE -->|Single Request| REFRESH[POST /auth/refresh]
    REFRESH -->|Replay with new JWT| API[Protected Backend Endpoints]""",
        ),
        causal_graph=CausalGraphData(nodes=causal_nodes, edges=causal_edges),
        grounded_concept=GroundedConcept(
            name="HTTP Interceptor with Mutex Promise Queue",
            headline="Preventing Refresh Stampedes in Single-Page Applications",
            what_it_is="An architectural pattern where outbound HTTP calls and inbound responses are intercepted transparently. When an authorization failure occurs, concurrent requests are queued into an in-memory promise array until a single refresh operation completes, after which all pending requests are replayed.",
            how_your_repo_uses_it="In src/api/interceptors/tokenRefresh.ts, the agent wraps Axios with a failedQueue array and isRefreshing boolean flag, eliminating the 7 duplicate catch blocks in UI components.",
            code_snippet="""// Grounded in src/api/interceptors/tokenRefresh.ts
if (isRefreshing) {
  return new Promise((resolve, reject) => {
    failedQueue.push({ resolve, reject });
  }).then(token => {
    originalRequest.headers['Authorization'] = 'Bearer ' + token;
    return axiosInstance(originalRequest);
  });
}""",
            pitfalls_to_watch=[
                "Forgetting to check if the failing request is the refresh endpoint itself (triggers infinite recursion).",
                "Memory leaks if rejected promises in the queue are never settled.",
                "Clearing stale credentials on hard 403 Forbidden vs retryable 401 Unauthorized.",
            ],
            related_concepts=["JWT Rotation", "Middleware Pattern", "Token Bucket", "Circuit Breaker"],
        ),
        attention_items=[
            AttentionItem(
                title="Critical Risk: Verify Recursive Refresh Loop Breakpoint",
                level="HIGH",
                detail="If the refresh token itself has expired and returns a 401, the interceptor must trigger immediate session termination instead of attempting another refresh.",
                action_required="Verify originalRequest.url.includes('/auth/refresh') check is present before retrying.",
                jev_attention_probability=0.97,
                llm_summary="Security Review for attachTokenRefreshInterceptor: Token refresh failure could trigger infinite recursion without explicit endpoint exclusion.",
                symbol="attachTokenRefreshInterceptor",
                file_path="src/api/interceptors/tokenRefresh.ts",
                hunk_id="hunk_auth_1",
                diff_snippet="""+ export function attachTokenRefreshInterceptor(axiosInstance: AxiosInstance) {
+   axiosInstance.interceptors.response.use(
+     response => response,
+     async error => {
+       const originalRequest = error.config;
+       if (error.response?.status === 401 && !originalRequest._retry) {
+         if (originalRequest.url.includes('/auth/refresh')) {
+           logoutUser();
+           return Promise.reject(error);
+         }
+         originalRequest._retry = true;
+       }
+       return Promise.reject(error);
+     }
+   );
+ }""",
                graph_context={
                    "affected_callers": ["HttpClient", "AuthService", "UserProfile", "BillingDashboard"],
                    "unattended_callers": ["legacyLoginHandler"],
                    "coupled_services": ["HttpClient", "AuthService", "UserProfile", "BillingDashboard"],
                    "blast_radius_level": "system_critical",
                    "blast_radius_score": 4.0,
                },
                llm_analysis={
                    "summary": "Security Review for attachTokenRefreshInterceptor: Token refresh failure could trigger infinite recursion without explicit endpoint exclusion.",
                    "why_at_risk": "Modifications to `attachTokenRefreshInterceptor` intercept all application HTTP responses globally. If the refresh request itself returns a 401 Unauthorized status, absence of an explicit endpoint escape check will trigger an infinite circular retry loop that floods the auth server and exhausts browser memory.",
                    "jev_signals_breakdown": "Classified as SECURITY_RELEVANT_CHANGE with system_critical blast radius (score 4.0/5.0). Human review probability is 97% with high attention priority.",
                    "code_change_breakdown": "Added global Axios response interceptor managing response rejection handlers, retry flags, and async request queueing.",
                    "change_graph_breakdown": "Change graph impacts HttpClient and couples 4 downstream services (AuthService, UserProfile, BillingDashboard). Warning: 1 legacy caller still bypasses centralized interceptor.",
                    "recommended_verification": "Simulate an expired refresh token returning HTTP 401 and verify immediate redirect to logout with zero recursive retries.",
                    "source": "Gemini LLM (System 2 Grounded)",
                },
            ),
            AttentionItem(
                title="Medium Risk: Stale State in Redux/Zustand Store",
                level="MEDIUM",
                detail="When the token refreshes automatically, local state management stores must be updated with the new expiration timestamp.",
                action_required="Dispatch authStore.setToken() inside the interceptor resolution handler.",
                jev_attention_probability=0.81,
                llm_summary="Senior Review Required for tokenRefresh: Store synchronization needed upon background token renewal.",
                symbol="failedQueue.resolve",
                file_path="src/api/interceptors/tokenRefresh.ts",
                hunk_id="hunk_auth_1",
                diff_snippet="""+ let failedQueue: Array<{ resolve: (token: string) => void; reject: (err: any) => void }> = [];
+ // Mutex queue drains on refresh success, updating bearer headers""",
                graph_context={
                    "affected_callers": ["authStore", "UserProfile"],
                    "unattended_callers": [],
                    "coupled_services": ["AuthService", "UserProfile"],
                    "blast_radius_level": "localized",
                    "blast_radius_score": 2.8,
                },
                llm_analysis={
                    "summary": "Senior Review Required for tokenRefresh: Store synchronization needed upon background token renewal.",
                    "why_at_risk": "While Axios headers are updated transparently on token renewal, front-end state stores (Redux/Zustand) can desynchronize if not informed of the new token expiry, leading to premature UI logout prompts.",
                    "jev_signals_breakdown": "Human review probability is 81% (exceeds 80% threshold). Localized blast radius with medium state-coupling risk.",
                    "code_change_breakdown": "Replaced individual component refresh catches with an in-memory Promise resolution queue.",
                    "change_graph_breakdown": "Directly impacts authentication storage subscribers across UI components.",
                    "recommended_verification": "Inspect state management subscriber triggers following successful 401 interceptor replay.",
                    "source": "AgentTrace Grounded Risk Synthesis (Local)",
                },
            ),
        ],
    )
