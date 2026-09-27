/**
 * AgentTrace Marketing Site Main Scripts
 */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Copy-to-clipboard functionality
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const text = button.getAttribute('data-copy');
      if (!text) return;

      try {
        await navigator.clipboard.writeText(text);
        const originalContent = button.innerHTML;
        button.innerHTML = `
          <span class="material-symbols-outlined text-emerald-400 text-sm">check</span>
          <span class="text-emerald-400 font-mono text-xs">Copied!</span>
        `;
        setTimeout(() => {
          button.innerHTML = originalContent;
        }, 2000);
      } catch (err) {
        console.error('Failed to copy to clipboard', err);
      }
    });
  });

  // 2. Interactive OS / Tool tabs in Quickstart
  document.querySelectorAll('[data-tab-group]').forEach(group => {
    const groupName = group.getAttribute('data-tab-group');
    const buttons = group.querySelectorAll('[data-tab-btn]');
    const targets = document.querySelectorAll(`[data-tab-content="${groupName}"]`);

    buttons.forEach(btn => {
      btn.addEventListener('click', () => {
        const targetId = btn.getAttribute('data-tab-btn');

        // Update active buttons
        buttons.forEach(b => {
          b.classList.remove('bg-indigo-600', 'text-white', 'border-indigo-500');
          b.classList.add('bg-slate-800', 'text-slate-400', 'border-slate-700');
        });
        btn.classList.add('bg-indigo-600', 'text-white', 'border-indigo-500');
        btn.classList.remove('bg-slate-800', 'text-slate-400', 'border-slate-700');

        // Show matching content
        targets.forEach(content => {
          if (content.getAttribute('data-tab-id') === targetId) {
            content.classList.remove('hidden');
          } else {
            content.classList.add('hidden');
          }
        });
      });
    });
  });

  // 3. Initialize Embedded Interactive Demo on page if container exists
  if (document.getElementById('demo-workspace')) {
    new AgentTraceDemo({
      containerId: 'demo-workspace',
      initialScenario: 'payment'
    });
  }

  // 4. Mobile Menu Toggle
  const mobileMenuBtn = document.getElementById('mobile-menu-btn');
  const mobileMenu = document.getElementById('mobile-menu');
  if (mobileMenuBtn && mobileMenu) {
    mobileMenuBtn.addEventListener('click', () => {
      mobileMenu.classList.toggle('hidden');
    });
  }
});
