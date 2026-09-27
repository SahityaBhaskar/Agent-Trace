/**
 * AgentTrace In-Browser Interactive Demo Engine
 * Powers zero-backend client-side demo runs on Vercel
 */

class AgentTraceDemo {
  constructor(options = {}) {
    this.containerId = options.containerId || 'demo-workspace';
    this.scenarios = {};
    this.currentScenarioId = options.initialScenario || 'payment';
    this.activeTab = 'causal';
    this.selectedNodeId = null;
    this.init();
  }

  async init() {
    try {
      // Helper for path resolution across different hostings / subdirectories
      const fetchAsset = async (filename) => {
        const candidates = [
          `/assets/data/${filename}`,
          `assets/data/${filename}`,
          `../assets/data/${filename}`
        ];
        for (const url of candidates) {
          try {
            const res = await fetch(url);
            if (res.ok) return await res.json();
          } catch (e) {}
        }
        throw new Error(`Could not load ${filename}`);
      };

      // Load bundled scenario data
      const [paymentData, authData] = await Promise.all([
        fetchAsset('payment_retry.json'),
        fetchAsset('auth_interceptor.json')
      ]);

      this.scenarios['payment'] = paymentData;
      this.scenarios['auth'] = authData;

      this.render();
    } catch (err) {
      console.error('Failed to load demo scenario JSON:', err);
      const container = document.getElementById(this.containerId);
      if (container) {
        container.innerHTML = `
          <div class="p-8 text-center text-rose-400">
            <span class="material-symbols-outlined text-4xl mb-2">error</span>
            <p>Failed to load demo scenario data. Please check network connection.</p>
          </div>
        `;
      }
    }
  }

  switchScenario(scenarioId) {
    if (this.scenarios[scenarioId]) {
      this.currentScenarioId = scenarioId;
      this.selectedNodeId = null;
      this.render();
    }
  }

  switchTab(tabId) {
    this.activeTab = tabId;
    this.render();
  }

  selectNode(nodeId) {
    this.selectedNodeId = nodeId;
    this.render();
  }

  render() {
    const container = document.getElementById(this.containerId);
    if (!container) return;

    const data = this.scenarios[this.currentScenarioId];
    if (!data) return;

    container.innerHTML = `
      <!-- Top Demo Bar -->
      <div class="flex flex-wrap items-center justify-between gap-4 p-4 border-b border-slate-800 bg-slate-900/90 backdrop-blur rounded-t-xl">
        <div class="flex items-center gap-3">
          <div class="flex items-center gap-2">
            <span class="pulse-dot pulse-dot-cyan"></span>
            <span class="text-xs font-mono font-semibold uppercase tracking-wider text-cyan-400">Live Interactive Demo</span>
          </div>
          <span class="text-slate-600">|</span>
          <div class="flex items-center gap-1.5 bg-slate-800/80 px-2.5 py-1 rounded-lg border border-slate-700">
            <span class="text-xs text-slate-400">Active Scenario:</span>
            <select id="scenario-selector" class="bg-transparent text-xs font-semibold text-white focus:outline-none cursor-pointer">
              <option value="payment" ${this.currentScenarioId === 'payment' ? 'selected' : ''} class="bg-slate-900 text-white">Payment Retry Refactor (Claude Code)</option>
              <option value="auth" ${this.currentScenarioId === 'auth' ? 'selected' : ''} class="bg-slate-900 text-white">Auth Token Interceptor (Claude Code)</option>
            </select>
          </div>
        </div>

        <!-- Telemetry Stats -->
        <div class="flex items-center gap-3 text-xs font-mono">
          <div class="bg-slate-800/60 px-2.5 py-1 rounded border border-slate-700/60 text-slate-300">
            Files: <strong class="text-white">${data.stats.files_inspected}</strong>
          </div>
          <div class="bg-slate-800/60 px-2.5 py-1 rounded border border-slate-700/60 text-slate-300">
            Paths: <strong class="text-white">${data.stats.relevant_paths}</strong>
          </div>
          <div class="bg-emerald-950/40 px-2.5 py-1 rounded border border-emerald-800/50 text-emerald-400 flex items-center gap-1">
            <span class="material-symbols-outlined text-sm">verified</span>
            Tests: ${data.stats.tests_run}/${data.stats.tests_run}
          </div>
          <div class="bg-indigo-950/40 px-2.5 py-1 rounded border border-indigo-800/50 text-indigo-300 flex items-center gap-1">
            <span class="material-symbols-outlined text-sm">speed</span>
            Jev: 434ms
          </div>
        </div>
      </div>

      <!-- User Prompt Banner -->
      <div class="p-4 bg-slate-950/70 border-b border-slate-800/80 flex items-center gap-3">
        <span class="material-symbols-outlined text-indigo-400 text-xl">prompt_suggestion</span>
        <div class="text-xs font-mono">
          <span class="text-slate-400 uppercase tracking-wider text-[10px] block">Agent Trigger Prompt:</span>
          <span class="text-slate-100 font-semibold text-sm">"${data.user_prompt}"</span>
        </div>
      </div>

      <!-- Navigation Tabs -->
      <div class="flex items-center gap-2 px-4 pt-3 border-b border-slate-800 bg-slate-900/40 overflow-x-auto">
        ${this.renderTabBtn('causal', 'account_tree', 'Causal Lineage & "Ask Why"')}
        ${this.renderTabBtn('blast', 'radar', 'Blast Radius & Diff')}
        ${this.renderTabBtn('checklist', 'checklist', 'Review Checklist')}
        ${this.renderTabBtn('flow', 'compare_arrows', 'Execution Flow')}
        ${this.renderTabBtn('learning', 'school', 'Learning Hub')}
      </div>

      <!-- Tab Content Area -->
      <div class="p-5 min-h-[460px] bg-slate-950/40">
        ${this.renderTabContent(data)}
      </div>

      <!-- Footer Bar -->
      <div class="px-5 py-3 border-t border-slate-800 bg-slate-900/60 flex flex-wrap items-center justify-between text-xs text-slate-400 rounded-b-xl font-mono">
        <div class="flex items-center gap-2">
          <span>Agent ID: <strong class="text-slate-200">${data.agent_id}</strong></span>
          <span>•</span>
          <span>Session: <strong class="text-slate-200">${data.id}</strong></span>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-slate-500">Ready to run this on your local codebase?</span>
          <a href="/docs" class="text-cyan-400 hover:underline flex items-center gap-1">
            Setup Guide <span class="material-symbols-outlined text-sm">arrow_forward</span>
          </a>
        </div>
      </div>
    `;

    // Attach event listeners
    const selector = container.querySelector('#scenario-selector');
    if (selector) {
      selector.addEventListener('change', (e) => this.switchScenario(e.target.value));
    }

    container.querySelectorAll('[data-demo-tab]').forEach(btn => {
      btn.addEventListener('click', () => this.switchTab(btn.dataset.demoTab));
    });

    container.querySelectorAll('[data-node-id]').forEach(card => {
      card.addEventListener('click', () => this.selectNode(card.dataset.nodeId));
    });
  }

  renderTabBtn(id, icon, label) {
    const isActive = this.activeTab === id;
    const activeClasses = isActive 
      ? 'border-indigo-500 text-indigo-300 bg-indigo-950/30' 
      : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-800/40';

    return `
      <button data-demo-tab="${id}" class="flex items-center gap-2 px-3.5 py-2.5 border-b-2 text-xs font-medium rounded-t-lg transition whitespace-nowrap ${activeClasses}">
        <span class="material-symbols-outlined text-base">${icon}</span>
        <span>${label}</span>
      </button>
    `;
  }

  renderTabContent(data) {
    switch (this.activeTab) {
      case 'causal':
        return this.renderCausalTab(data);
      case 'blast':
        return this.renderBlastTab(data);
      case 'checklist':
        return this.renderChecklistTab(data);
      case 'flow':
        return this.renderFlowTab(data);
      case 'learning':
        return this.renderLearningTab(data);
      default:
        return `<p class="text-slate-400">Select a tab</p>`;
    }
  }

  renderCausalTab(data) {
    const nodes = data.causal_graph.nodes;
    const selectedNode = this.selectedNodeId 
      ? nodes.find(n => n.node_id === this.selectedNodeId) 
      : nodes[nodes.length - 2] || nodes[0];

    return `
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-5">
        <!-- Event Sequence Graph -->
        <div class="lg:col-span-7 space-y-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-mono uppercase text-slate-400 tracking-wider">Agent Event Chain (Click to Inspect)</span>
            <span class="text-[11px] text-slate-500 font-mono">${nodes.length} Causal Nodes</span>
          </div>

          <div class="space-y-2 max-h-[480px] overflow-y-auto pr-1">
            ${nodes.map((node, idx) => {
              const isSelected = selectedNode && selectedNode.node_id === node.node_id;
              const isAction = node.action_type.includes('CREATED') || node.action_type.includes('MODIFIED');
              const isReasoning = node.action_type.includes('REASONING');
              
              let borderClass = isSelected ? 'border-indigo-500 bg-indigo-950/20' : 'border-slate-800 bg-slate-900/60 hover:border-slate-700';
              let badgeColor = isAction ? 'text-amber-400 bg-amber-950/30 border-amber-800/40' : (isReasoning ? 'text-cyan-400 bg-cyan-950/30 border-cyan-800/40' : 'text-slate-400 bg-slate-800 border-slate-700');

              return `
                <div data-node-id="${node.node_id}" class="p-3 rounded-lg border cursor-pointer transition flex items-start justify-between gap-3 ${borderClass}">
                  <div class="flex items-start gap-3">
                    <span class="text-xs font-mono font-bold text-slate-500 mt-0.5">#${idx + 1}</span>
                    <div>
                      <div class="flex items-center gap-2 mb-1">
                        <span class="text-[10px] font-mono px-2 py-0.5 rounded border ${badgeColor}">${node.action_type}</span>
                        <span class="text-xs font-semibold text-slate-200">${node.label}</span>
                      </div>
                      <p class="text-xs text-slate-400 line-clamp-1">${node.description}</p>
                    </div>
                  </div>
                  <span class="material-symbols-outlined text-slate-500 text-sm">visibility</span>
                </div>
              `;
            }).join('')}
          </div>
        </div>

        <!-- Grounded "Ask Why" Panel -->
        <div class="lg:col-span-5 bg-slate-900/80 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between pb-3 border-b border-slate-800 mb-4">
              <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-cyan-400">psychology</span>
                <span class="text-xs font-mono font-bold text-slate-200 uppercase">Grounded "Ask Why" Reasoning</span>
              </div>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-950/50 text-cyan-300 border border-cyan-800/50">Gemini System 2</span>
            </div>

            ${selectedNode ? `
              <div class="space-y-4">
                <div>
                  <span class="text-[11px] font-mono text-slate-400 block mb-1">Event Target:</span>
                  <div class="bg-slate-950 px-3 py-2 rounded border border-slate-800 text-xs font-mono text-cyan-300">
                    ${selectedNode.label}
                  </div>
                </div>

                <div>
                  <span class="text-[11px] font-mono text-slate-400 block mb-1">Why Did the Agent Do This?</span>
                  <div class="bg-indigo-950/30 border border-indigo-800/40 p-3 rounded-lg text-xs text-indigo-100 leading-relaxed">
                    ${selectedNode.why_explanation || selectedNode.description}
                  </div>
                </div>

                <div>
                  <span class="text-[11px] font-mono text-slate-400 block mb-1">Epistemic Status:</span>
                  <span class="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                    ${selectedNode.epistemic_status} (Derived from Agent Tool Transcripts)
                  </span>
                </div>
              </div>
            ` : `
              <p class="text-xs text-slate-400">Click any event on the left to inspect why the agent performed that action.</p>
            `}
          </div>

          <div class="mt-4 pt-3 border-t border-slate-800/80 text-[11px] text-slate-500 font-mono flex items-center gap-1.5">
            <span class="material-symbols-outlined text-sm text-emerald-400">verified</span>
            100% grounded in AST & tool execution logs
          </div>
        </div>
      </div>
    `;
  }

  renderBlastTab(data) {
    const changes = data.logical_changes || [];
    return `
      <div class="space-y-5">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          ${changes.map(ch => `
            <div class="p-3.5 bg-slate-900/70 border border-slate-800 rounded-lg">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-950/40 text-indigo-300 border border-indigo-800/40">${ch.category}</span>
                <span class="text-[10px] font-mono text-rose-400">${ch.blast_radius}</span>
              </div>
              <h4 class="text-xs font-semibold text-slate-200 mb-1">${ch.title}</h4>
              <p class="text-[11px] text-slate-400 line-clamp-2">${ch.why_explanation}</p>
            </div>
          `).join('')}
        </div>

        <!-- Diff Viewer -->
        <div class="border border-slate-800 rounded-xl overflow-hidden">
          <div class="bg-slate-900 px-4 py-2 border-b border-slate-800 flex items-center justify-between text-xs font-mono">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-slate-400 text-sm">code</span>
              <span class="text-slate-200 font-semibold">Affected Code Changes (Diff Hunks)</span>
            </div>
            <span class="text-slate-500">AST Verified</span>
          </div>

          <div class="p-4 space-y-4 max-h-[360px] overflow-y-auto font-mono text-xs">
            ${changes.map(ch => ch.diff_hunks ? ch.diff_hunks.map(hunk => `
              <div class="bg-slate-950 rounded border border-slate-800 p-3">
                <div class="text-[11px] text-cyan-400 font-bold mb-2">${hunk.file_path} • @@ -${hunk.old_start} +${hunk.new_start} @@</div>
                <div class="diff-del font-mono text-[11px]">${hunk.old_code.replace(/</g, '&lt;')}</div>
                <div class="diff-add font-mono text-[11px]">${hunk.new_code.replace(/</g, '&lt;')}</div>
              </div>
            `).join('') : '').join('')}
          </div>
        </div>
      </div>
    `;
  }

  renderChecklistTab(data) {
    const items = data.checklist || [];
    const attention = data.attention_items || [];

    return `
      <div class="space-y-5">
        ${attention.length > 0 ? `
          <div class="bg-rose-950/20 border border-rose-800/40 p-4 rounded-xl">
            <div class="flex items-center gap-2 mb-2 text-rose-300 font-semibold text-xs uppercase font-mono">
              <span class="material-symbols-outlined text-base">warning</span>
              Developer Attention Required
            </div>
            ${attention.map(item => `
              <div class="mb-2 last:mb-0">
                <strong class="text-xs text-rose-200">${item.title}</strong>
                <p class="text-xs text-rose-300/80 mt-0.5">${item.detail}</p>
                <div class="mt-1 text-[11px] text-cyan-300 font-mono">Action: ${item.action_required}</div>
              </div>
            `).join('')}
          </div>
        ` : ''}

        <div class="space-y-2">
          <span class="text-xs font-mono uppercase text-slate-400 tracking-wider block mb-2">Automated Production Readiness Checks</span>
          ${items.map(item => `
            <div class="flex items-start gap-3 p-3 bg-slate-900/60 border border-slate-800 rounded-lg">
              <span class="material-symbols-outlined ${item.passed ? 'text-emerald-400' : 'text-amber-400'} text-lg mt-0.5">
                ${item.passed ? 'check_circle' : 'flag'}
              </span>
              <div>
                <span class="text-xs font-semibold text-slate-200">${item.item}</span>
                <p class="text-xs text-slate-400 mt-0.5">${item.explanation}</p>
              </div>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  renderFlowTab(data) {
    const flow = data.execution_flow;
    if (!flow) return `<p class="text-slate-400 text-xs">No execution flow diff available for this scenario.</p>`;

    return `
      <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
        <!-- Before -->
        <div class="p-4 bg-slate-900/70 border border-slate-800 rounded-xl">
          <div class="flex items-center justify-between pb-2 mb-3 border-b border-slate-800">
            <span class="text-xs font-mono font-bold text-rose-400 uppercase">Original Execution Flow</span>
            <span class="text-[10px] font-mono text-slate-500">Unprotected</span>
          </div>
          <p class="text-xs text-slate-400 mb-3">${flow.before.summary}</p>
          <div class="space-y-2 font-mono text-xs">
            ${flow.before.steps.map((st, i) => `
              <div class="p-2 rounded bg-slate-950 border border-slate-800 flex items-center gap-2">
                <span class="text-slate-600 font-bold">${i + 1}.</span>
                <span class="text-slate-300">${st.name}</span>
              </div>
            `).join('')}
          </div>
        </div>

        <!-- After -->
        <div class="p-4 bg-slate-900/70 border border-indigo-900/50 rounded-xl">
          <div class="flex items-center justify-between pb-2 mb-3 border-b border-slate-800">
            <span class="text-xs font-mono font-bold text-emerald-400 uppercase">Agent-Refactored Flow</span>
            <span class="text-[10px] font-mono text-indigo-400">Hardened</span>
          </div>
          <p class="text-xs text-slate-300 mb-3">${flow.after.summary}</p>
          <div class="space-y-2 font-mono text-xs">
            ${flow.after.steps.map((st, i) => `
              <div class="p-2 rounded bg-indigo-950/20 border border-indigo-800/40 flex items-center gap-2">
                <span class="text-indigo-400 font-bold">${i + 1}.</span>
                <span class="text-emerald-300">${st.name}</span>
              </div>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }

  renderLearningTab(data) {
    const concepts = data.grounded_concepts || [];
    return `
      <div class="space-y-4">
        <div class="flex items-center justify-between">
          <div>
            <h4 class="text-sm font-semibold text-slate-200">Codebase-Grounded Learning Hub</h4>
            <p class="text-xs text-slate-400">Architectural patterns extracted and explained from the code the agent touched.</p>
          </div>
          <span class="badge badge-cyan">Repo-Grounded</span>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          ${concepts.map(concept => `
            <div class="p-4 bg-slate-900/80 border border-slate-800 rounded-xl space-y-3">
              <div class="flex items-center justify-between">
                <h5 class="text-xs font-bold text-slate-100 uppercase tracking-wider">${concept.concept_name}</h5>
                <span class="text-[10px] font-mono text-indigo-400">${concept.category}</span>
              </div>
              <p class="text-xs text-slate-300 leading-relaxed">${concept.explanation}</p>
              ${concept.code_snippet ? `
                <div class="bg-slate-950 p-2.5 rounded border border-slate-800 text-[11px] font-mono text-slate-400 overflow-x-auto">
                  ${concept.code_snippet.replace(/</g, '&lt;')}
                </div>
              ` : ''}
              ${concept.pitfalls ? `
                <div class="text-[11px] text-amber-300/90 font-mono bg-amber-950/20 p-2 rounded border border-amber-800/30">
                  ⚠️ Pitfall: ${concept.pitfalls}
                </div>
              ` : ''}
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }
}

// Global initialization helper
window.AgentTraceDemo = AgentTraceDemo;
