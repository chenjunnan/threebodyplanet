const runs = document.querySelector('#runs');
const validation = document.querySelector('#validation');
const state = document.querySelector('#validation-state');

function escapeText(value) {
  const node = document.createElement('span');
  node.textContent = String(value ?? '—');
  return node.innerHTML;
}

async function loadRuns() {
  runs.innerHTML = '<p class="muted">读取中…</p>';
  try {
    const response = await fetch('/api/v1/runs');
    const data = await response.json();
    if (!data.runs.length) {
      runs.innerHTML = '<p class="muted">归档根目录中没有可识别运行。</p>';
      return;
    }
    runs.innerHTML = data.runs.map((run) => `
      <button class="run-card" data-run="${escapeText(run.run_id)}">
        <span><strong>${escapeText(run.run_id)}</strong><small>${escapeText(run.world_id)}</small></span>
        <span class="run-meta"><b>${escapeText(run.status)}</b><small>t = ${escapeText(run.simulation_time_s)} s</small></span>
      </button>`).join('');
  } catch (error) {
    runs.innerHTML = `<p class="error">${escapeText(error.message)}</p>`;
  }
}

document.querySelector('#refresh').addEventListener('click', loadRuns);
document.querySelector('#validate').addEventListener('click', async () => {
  validation.textContent = '';
  state.textContent = '后端验证中…';
  try {
    const config = JSON.parse(document.querySelector('#config').value);
    const response = await fetch('/api/v1/experiments/validate', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({config})
    });
    const data = await response.json();
    state.textContent = data.valid ? `有效 · ${data.sha256.slice(0, 12)}` : '验证失败';
    validation.textContent = JSON.stringify(data.valid ? data.resolved : data.errors, null, 2);
  } catch (error) {
    state.textContent = '输入不是有效 JSON';
    validation.textContent = error.message;
  }
});

loadRuns();
