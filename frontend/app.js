let selectedFile = null;
let selectedSampleFilename = null;
let _previewBlobUrl = null;

// ── API key (session only) ────────────────────────────────────────────────────

function saveApiKey() {
  const key = document.getElementById('api-key-input').value.trim();
  if (!key) return;
  sessionStorage.setItem('anthropic_key', key);
  document.getElementById('api-key-input').value = '';
  document.getElementById('key-unset').classList.add('hidden');
  document.getElementById('key-set').classList.remove('hidden');
  document.getElementById('key-set').classList.add('flex');
}

function clearApiKey() {
  sessionStorage.removeItem('anthropic_key');
  document.getElementById('key-set').classList.add('hidden');
  document.getElementById('key-set').classList.remove('flex');
  document.getElementById('key-unset').classList.remove('hidden');
}

function getApiKey() {
  return sessionStorage.getItem('anthropic_key') || '';
}

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  loadSamples();
  loadPlaybook();
  // Restore key state if page is refreshed mid-session
  if (getApiKey()) {
    document.getElementById('key-unset').classList.add('hidden');
    document.getElementById('key-set').classList.remove('hidden');
    document.getElementById('key-set').classList.add('flex');
  }
});

// ── Modals ────────────────────────────────────────────────────────────────────

function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
  document.body.style.overflow = '';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    ['playbook-modal', 'howitworks-modal'].forEach(closeModal);
  }
});

// ── Tabs ──────────────────────────────────────────────────────────────────────

function switchTab(tab) {
  document.getElementById('tab-content-upload').classList.toggle('hidden', tab !== 'upload');
  document.getElementById('tab-content-samples').classList.toggle('hidden', tab !== 'samples');
  document.getElementById('tab-upload').classList.toggle('active', tab === 'upload');
  document.getElementById('tab-samples').classList.toggle('active', tab === 'samples');
  hideUploadError();
}

// ── Samples ───────────────────────────────────────────────────────────────────

async function loadSamples() {
  try {
    const res = await fetch('/api/samples');
    const samples = await res.json();
    renderSampleGrid(samples, 'samples-grid');
  } catch (_) {}
}

function renderSampleGrid(samples, containerId) {
  const grid = document.getElementById(containerId);
  grid.innerHTML = '';
  samples.forEach(s => {
    const card = document.createElement('div');
    card.className = 'sample-card border border-gray-200 rounded-lg p-4 bg-white';
    card.dataset.filename = s.filename;
    card.innerHTML = `
      <div class="flex items-start justify-between gap-2 mb-2">
        <span class="text-sm font-medium text-gray-800">${esc(s.label)}</span>
        <span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 whitespace-nowrap flex-shrink-0">${esc(s.type)}</span>
      </div>
      <p class="text-xs text-gray-500 mb-2">${esc(s.description)}</p>
      <span class="text-xs text-indigo-500 font-medium">${esc(s.source)}</span>`;
    card.addEventListener('click', () => selectSample(s.filename, card));
    grid.appendChild(card);
  });
}


function selectSample(filename, cardEl) {
  document.querySelectorAll('.sample-card').forEach(c => c.classList.remove('selected'));
  cardEl.classList.add('selected');
  selectedSampleFilename = filename;
  document.getElementById('run-sample-btn').disabled = false;
}

async function submitSampleReview() {
  if (!selectedSampleFilename) return;
  try {
    const fileRes = await fetch(`/api/samples/${encodeURIComponent(selectedSampleFilename)}`);
    if (!fileRes.ok) throw new Error('Could not load sample file.');
    const blob = await fileRes.blob();
    // Preserve MIME type so the PDF iframe renders correctly
    const file = new File([blob], selectedSampleFilename, { type: blob.type });
    showProcessing(file);
    await runReview(file);
  } catch (err) {
    showUploadSection();
    showUploadError(err.message || 'Something went wrong.');
  }
}

// ── Drag & drop ──────────────────────────────────────────────────────────────

function handleDragOver(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.add('drag-over');
}

function handleDragLeave() {
  document.getElementById('drop-zone').classList.remove('drag-over');
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
}

function handleFileSelect(e) {
  const file = e.target.files[0];
  if (file) setFile(file);
}

function setFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf', 'docx', 'doc'].includes(ext)) {
    showUploadError('Please upload a PDF or DOCX file.');
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showUploadError('File exceeds 10 MB limit.');
    return;
  }
  hideUploadError();
  selectedFile = file;
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-size').textContent = formatBytes(file.size);
  document.getElementById('file-selected').classList.remove('hidden');
}

async function submitUploadReview() {
  if (!selectedFile) return;
  showProcessing(selectedFile);
  try {
    await runReview(selectedFile);
  } catch (err) {
    showUploadSection();
    showUploadError(err.message || 'Something went wrong. Please try again.');
  }
}

// ── Core streaming review ─────────────────────────────────────────────────────

async function runReview(file) {
  const formData = new FormData();
  formData.append('file', file);

  const headers = {};
  const key = getApiKey();
  if (key) headers['X-Api-Key'] = key;

  const res = await fetch('/api/review', { method: 'POST', body: formData, headers });

  // Non-streaming error (validation failures before the stream starts)
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: `Server error ${res.status}` }));
    throw new Error(data.detail || `Server error ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    // SSE lines are separated by double newlines
    const parts = buffer.split('\n\n');
    buffer = parts.pop(); // keep incomplete trailing chunk

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data: ')) continue;
      let event;
      try {
        event = JSON.parse(line.slice(6));
      } catch (_) {
        continue; // malformed JSON — skip
      }
      handleStreamEvent(event); // errors from here must propagate
    }
  }
}

// ── SSE event handlers ────────────────────────────────────────────────────────

function handleStreamEvent(event) {
  switch (event.type) {
    case 'meta':
      // Step 1 complete — transition to results view, show skeleton
      markStep(1, 'done');
      markStep(2, 'done');
      markStep(3, 'active');
      initResultsSection(event);
      break;

    case 'verdict':
      renderVerdict(event);
      break;

    case 'clause':
      appendClauseRow(event);
      break;

    case 'approved':
      renderAutoApproved(event.auto_approved_clauses);
      break;

    case 'done':
      markStep(3, 'done');
      finaliseResults();
      break;

    case 'error':
      throw new Error(event.detail || 'Analysis failed.');
  }
}

// ── Progressive results rendering ─────────────────────────────────────────────

function initResultsSection(meta) {
  // Transition: hide processing section, show results immediately
  document.getElementById('processing-section').classList.add('hidden');
  document.getElementById('results-section').classList.remove('hidden');

  document.getElementById('res-type').textContent = meta.contract_type || '';
  document.getElementById('res-parties').textContent =
    meta.parties && meta.parties.length ? meta.parties.join(' ↔ ') : 'Parties not identified';
  document.getElementById('res-date').textContent = meta.effective_date
    ? `Effective: ${meta.effective_date}` : 'Effective date not stated';

  // Skeleton placeholders until verdict arrives
  document.getElementById('res-risk').textContent = '…';
  document.getElementById('res-risk').className = 'px-3 py-1 rounded-full text-sm font-semibold bg-gray-100 text-gray-400';
  document.getElementById('res-action').textContent = '…';
  document.getElementById('res-action').className = 'px-3 py-1 rounded-full text-sm font-semibold bg-gray-100 text-gray-400';
  document.getElementById('res-summary').textContent = 'Analysing clauses…';

  // Show clause loading indicator
  document.getElementById('clause-loading').classList.remove('hidden');
  document.getElementById('result-actions').classList.add('hidden');
  document.getElementById('auto-approved-section').classList.add('hidden');
}

function renderVerdict(v) {
  const riskEl = document.getElementById('res-risk');
  riskEl.textContent = v.risk_level;
  riskEl.className = `px-3 py-1 rounded-full text-sm font-semibold badge-${v.risk_level.toLowerCase()}`;

  const actionEl = document.getElementById('res-action');
  actionEl.textContent = v.recommended_action;
  const actionKey = { 'Auto-approve': 'auto', 'Fast-track': 'fast', 'Full review': 'full', 'Escalate': 'escalate' }[v.recommended_action] || 'full';
  actionEl.className = `px-3 py-1 rounded-full text-sm font-semibold badge-${actionKey}`;

  document.getElementById('res-summary').textContent = v.executive_summary || '';
}

function appendClauseRow(c) {
  const clauseList = document.getElementById('clause-list');
  const row = buildClauseRow(c);
  // Insert before the loading indicator
  const loader = document.getElementById('clause-loading');
  clauseList.insertBefore(row, loader);
}

function renderAutoApproved(clauses) {
  const autoList = document.getElementById('auto-approved-list');
  autoList.innerHTML = '';
  if (clauses && clauses.length > 0) {
    clauses.forEach(name => {
      const tag = document.createElement('span');
      tag.className = 'bg-green-50 text-green-700 text-xs font-medium px-3 py-1 rounded-full border border-green-200';
      tag.textContent = name;
      autoList.appendChild(tag);
    });
    document.getElementById('auto-approved-section').classList.remove('hidden');
  }
}

function finaliseResults() {
  document.getElementById('clause-loading').classList.add('hidden');
  document.getElementById('result-actions').classList.remove('hidden');
}

// ── Clause row builder ────────────────────────────────────────────────────────

function buildClauseRow(c) {
  const statusConfig = {
    'Standard': { dot: 'bg-green-500', cls: 'status-standard' },
    'Minor deviation': { dot: 'bg-amber-400', cls: 'status-minor' },
    'Non-standard': { dot: 'bg-red-500', cls: 'status-nonstandard' },
    'Missing': { dot: 'bg-purple-500', cls: 'status-missing' },
  };
  const sc = statusConfig[c.status] || statusConfig['Standard'];
  const wrapper = document.createElement('div');
  wrapper.className = 'px-6 py-4 border-t border-gray-100 animate-fadeIn';

  if (c.status === 'Standard') {
    wrapper.innerHTML = `
      <div class="flex items-center gap-3">
        <span class="w-2.5 h-2.5 rounded-full ${sc.dot} flex-shrink-0"></span>
        <span class="text-sm font-medium text-gray-800">${esc(c.clause_name)}</span>
        <span class="ml-auto text-xs font-medium ${sc.cls}">${esc(c.status)}</span>
      </div>`;
    return wrapper;
  }

  const severityBadge = c.severity
    ? `<span class="text-xs px-2 py-0.5 rounded font-medium ${severityClass(c.severity)}">${c.severity} severity</span>` : '';

  wrapper.innerHTML = `
    <details>
      <summary class="flex items-center gap-3">
        <span class="w-2.5 h-2.5 rounded-full ${sc.dot} flex-shrink-0"></span>
        <span class="text-sm font-medium text-gray-800">${esc(c.clause_name)}</span>
        <div class="ml-auto flex items-center gap-2">
          ${severityBadge}
          <span class="text-xs font-medium ${sc.cls}">${esc(c.status)}</span>
          <svg class="chevron w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
          </svg>
        </div>
      </summary>
      <div class="mt-3 ml-5 space-y-3">
        ${c.issue ? `<p class="text-sm text-gray-600">${esc(c.issue)}</p>` : ''}
        ${c.suggested_redline ? `
          <div class="redline-box rounded-lg p-4">
            <p class="text-xs font-semibold text-orange-700 mb-1.5">Suggested redline</p>
            <p class="text-sm text-gray-700 leading-relaxed">${esc(c.suggested_redline)}</p>
          </div>` : ''}
      </div>
    </details>`;
  return wrapper;
}

function severityClass(s) {
  if (s === 'High') return 'bg-red-100 text-red-700';
  if (s === 'Medium') return 'bg-amber-100 text-amber-700';
  return 'bg-gray-100 text-gray-600';
}

// ── Step indicator helpers ────────────────────────────────────────────────────

function markStep(n, state) {
  const dot = document.getElementById(`step${n}-dot`);
  if (!dot) return;
  dot.classList.remove('active', 'done');
  if (state) dot.classList.add(state);
}

// ── Playbook ──────────────────────────────────────────────────────────────────

async function loadPlaybook() {
  try {
    const res = await fetch('/api/playbook');
    const rules = await res.json();
    const container = document.getElementById('playbook-rules');
    container.innerHTML = '';
    rules.forEach(r => {
      const div = document.createElement('div');
      div.className = 'playbook-item pl-4 py-2';
      div.innerHTML = `
        <p class="text-sm font-medium text-gray-800 mb-0.5">${esc(r.clause)}</p>
        <p class="text-sm text-gray-500">${esc(r.standard_position)}</p>`;
      container.appendChild(div);
    });
  } catch (_) {}
}

// ── UI state helpers ──────────────────────────────────────────────────────────

function showProcessing(file) {
  if (_previewBlobUrl) {
    URL.revokeObjectURL(_previewBlobUrl);
    _previewBlobUrl = null;
  }

  document.getElementById('upload-section').classList.add('hidden');
  document.getElementById('processing-section').classList.remove('hidden');
  document.getElementById('results-section').classList.add('hidden');
  [1, 2, 3].forEach(n => markStep(n, null));
  markStep(1, 'active');

  const filenameEl = document.getElementById('proc-filename');
  const previewEl = document.getElementById('proc-preview');
  const docxEl = document.getElementById('proc-docx');
  const iframeEl = document.getElementById('proc-pdf-iframe');

  filenameEl.textContent = file ? file.name : '';
  previewEl.classList.add('hidden');
  docxEl.classList.add('hidden');

  if (!file) return;

  const isPdf = file.name.toLowerCase().endsWith('.pdf');
  if (isPdf) {
    _previewBlobUrl = URL.createObjectURL(file);
    iframeEl.src = _previewBlobUrl;
    document.getElementById('proc-preview-label').textContent = file.name;
    previewEl.classList.remove('hidden');
  } else {
    document.getElementById('proc-docx-label').textContent = file.name;
    docxEl.classList.remove('hidden');
  }
}

function showUploadSection() {
  document.getElementById('upload-section').classList.remove('hidden');
  document.getElementById('processing-section').classList.add('hidden');
  document.getElementById('results-section').classList.add('hidden');
}

function showUploadError(msg) {
  const el = document.getElementById('upload-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

function hideUploadError() {
  document.getElementById('upload-error').classList.add('hidden');
}

function resetApp() {
  selectedFile = null;
  selectedSampleFilename = null;
  if (_previewBlobUrl) {
    URL.revokeObjectURL(_previewBlobUrl);
    _previewBlobUrl = null;
  }
  document.getElementById('file-input').value = '';
  document.getElementById('file-selected').classList.add('hidden');
  // Remove clause rows but keep #clause-loading (it lives inside clause-list)
  const clauseList = document.getElementById('clause-list');
  Array.from(clauseList.children).forEach(child => {
    if (child.id !== 'clause-loading') child.remove();
  });
  document.getElementById('clause-loading').classList.add('hidden');
  document.querySelectorAll('.sample-card').forEach(c => c.classList.remove('selected'));
  document.getElementById('run-sample-btn').disabled = true;
  hideUploadError();
  showUploadSection();
  switchTab('upload');
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
