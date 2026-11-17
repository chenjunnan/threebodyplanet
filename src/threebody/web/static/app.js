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

const jobDetails = document.querySelector('#job-details');
const jobState = document.querySelector('#job-state');
let activeRunId = null;
let pollHandle = null;

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || result.error || response.status);
  return result;
}

async function pollJob() {
  if (!activeRunId) return;
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(activeRunId)}`);
    if (!response.ok) throw new Error('当前进程没有该任务的活动记录');
    const info = await response.json();
    jobDetails.textContent = JSON.stringify(info, null, 2);
    jobState.textContent = `任务 ${info.run_id}: ${info.phase} · 物理状态 ${info.physical?.status ?? 'PENDING'}`;
    if (['COMPLETED','COLLISION','FAILED','CANCELLED','CANCELLED_BEFORE_START','PAUSED','WORKER_ERROR'].includes(info.phase)) {
      clearInterval(pollHandle);
      pollHandle = null;
      loadRuns();
    }
  } catch (error) {
    jobState.textContent = error.message;
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

function beginPoll(id) {
  activeRunId = id;
  clearInterval(pollHandle);
  pollJob();
  pollHandle = setInterval(pollJob, 1000);
}

document.querySelector('#start-run').addEventListener('click', async () => {
  try {
    const run_id = document.querySelector('#run-id').value;
    const initial_state = JSON.parse(document.querySelector('#initial-state').value);
    const settings = JSON.parse(document.querySelector('#long-settings').value);
    jobState.textContent = '提交真实M5A任务中…';
    const result = await apiPost('/api/v1/runs', {run_id, initial_state, settings});
    beginPoll(result.run_id);
  } catch (error) {
    jobState.textContent = `启动失败：${error.message}`;
  }
});

for (const action of ['pause','resume','cancel']) {
  document.querySelector(`#${action}-run`).addEventListener('click', async () => {
    const id = document.querySelector('#run-id').value;
    try {
      const result = await apiPost(`/api/v1/runs/${encodeURIComponent(id)}/${action}`, {});
      jobState.textContent = `服务端已接受${action}请求；物理状态需等待分段提交确认。`;
      beginPoll(result.run_id);
    } catch (error) {
      jobState.textContent = `请求失败：${error.message}`;
    }
  });
}
