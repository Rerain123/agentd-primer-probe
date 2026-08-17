const form = document.querySelector("#designForm");
const sequenceInput = document.querySelector("#sequence");
const sequenceCount = document.querySelector("#sequenceCount");
const sequenceMeta = document.querySelector(".sequence-meta");
const resultArea = document.querySelector("#resultArea");
const emptyState = document.querySelector("#emptyState");
const errorState = document.querySelector("#errorState");
const resultContent = document.querySelector("#resultContent");
const submitButton = document.querySelector("#submitButton");
const toolStatus = document.querySelector("#toolStatus");
const probeTypeInput = document.querySelector("#probeType");
const minimumTemplateLength = 80;
const batchPreflightForm = document.querySelector("#batchPreflightForm");
const batchFastaInput = document.querySelector("#batchFasta");
const batchSequenceCount = document.querySelector("#batchSequenceCount");
const manifestPanel = document.querySelector("#manifestPanel");
const manifestSummary = document.querySelector("#manifestSummary");
const manifestTargets = document.querySelector("#manifestTargets");
const manifestHint = document.querySelector("#manifestHint");
const confirmManifestButton = document.querySelector("#confirmManifestButton");
let activeManifestBatch = null;

const exampleSequence = "TGGGCGAACTTGGTCACCCCGAAGTATCTGATGAGATGATCACCGAGAGCCGGGGCGAGGAAGATGTACGGATACTTTCCGCACAGGGACTAGGTTAACCGCGATTTCTTATCCTGCGATAGCCGGCCGTGTAAACCTTTCTTAGGCATGGCAGAAAATGCAATCATATAACGGGGTTAGAAGGGAGCCTGTAGCATGCTGCCCGATTTCCCGTGTACCCCTGTCGCTGCGAAGTATATCCAGAGGTGCCGGTGCTAGCC";

function normalizeSequence(value) {
  return value.toUpperCase().replace(/[\s\d]/g, "");
}

function countFastaRecords(value) {
  const headers = String(value || "").split(/\r?\n/).filter(line => line.trim().startsWith(">")).length;
  return headers || (normalizeSequence(value).length ? 1 : 0);
}

function updateBatchSequenceCount() {
  const count = countFastaRecords(batchFastaInput.value);
  batchSequenceCount.textContent = `${count} 条`;
}

function manifestStatusLabel(status) {
  return {
    READY_DESIGN_ONLY: "基础设计",
    READY_FULL: "完整评估",
    NEEDS_CONFIRMATION: "需要确认",
    BLOCKED: "暂不可执行",
  }[status] || status || "未知";
}

function specificityInputValue(object) {
  if (!object) return "";
  return object.original_input?.name
    || object.original_input?.scientific_name
    || object.normalized_name
    || object.identifier
    || "";
}

function renderTargetManifest(batch) {
  activeManifestBatch = batch;
  const manifest = batch.target_manifest || batch.preflight?.target_manifest || {};
  const targets = batch.targets || manifest.targets || [];
  const counts = manifest.counts || {};
  const batchIssues = manifest.batch_issues || [];
  const problemTargetCount = (counts.NEEDS_CONFIRMATION || 0) + (counts.BLOCKED || 0);
  const problemCount = problemTargetCount + batchIssues.length;
  manifestPanel.hidden = false;
  manifestSummary.innerHTML = `<div class="manifest-summary-grid">
    <div class="manifest-summary-item"><span>全部靶标</span><strong>${targets.length}</strong></div>
    <div class="manifest-summary-item"><span>基础设计</span><strong>${counts.READY_DESIGN_ONLY || 0}</strong></div>
    <div class="manifest-summary-item"><span>完整评估</span><strong>${counts.READY_FULL || 0}</strong></div>
    <div class="manifest-summary-item"><span>需要处理</span><strong>${problemCount}</strong></div>
  </div>${batchIssues.length ? `<div class="manifest-batch-issues"><strong>整批问题</strong><ul>${batchIssues.map(issue => `<li>${escapeHtml(issue.message)}</li>`).join("")}</ul></div>` : ""}`;
  manifestTargets.innerHTML = targets.map(target => {
    const status = target.manifest_status || "BLOCKED";
    const issues = (target.issues || []).filter(issue => ["BLOCKER", "CONFIRMATION"].includes(issue.severity));
    const targetValue = specificityInputValue(target.target_object);
    const hostValue = specificityInputValue((target.host_objects || [])[0]);
    return `<article class="manifest-target ${target.requires_user_action ? "problem" : ""}" data-manifest-target="${escapeHtml(target.target_id)}">
      <div class="manifest-target-head">
        <div><h3>${escapeHtml(target.target_id)} · ${escapeHtml(target.template?.record_name || "未命名")}</h3>
        <p>${target.template?.length_nt || 0} nt · ${target.execution_path === "FULL_SPECIFICITY" ? "Primer3 + 规则 + 完整特异性" : target.execution_path === "DESIGN_ONLY" ? "Primer3 + 规则 + 去重排序" : "需先修正输入"}</p></div>
        <span class="manifest-badge ${status.toLowerCase().replaceAll("_", "-")}">${manifestStatusLabel(status)}</span>
      </div>
      ${issues.length ? `<ul class="manifest-issues">${issues.map(issue => `<li>${escapeHtml(issue.message)}</li>`).join("")}</ul>` : ""}
      <div class="manifest-edit-grid">
        ${target.requires_user_action ? `<label class="manifest-sequence-edit"><span>模板序列</span><textarea data-field="sequence" spellcheck="false">${escapeHtml(target.template?.sequence || "")}</textarea></label>` : ""}
        <label><span>记录名</span><input data-field="record_name" value="${escapeHtml(target.template?.record_name || "")}"></label>
        <label><span>靶标物种</span><input data-field="target_organism" value="${escapeHtml(targetValue)}" placeholder="可留空"></label>
        <label><span>宿主</span><input data-field="hosts" value="${escapeHtml(hostValue)}" placeholder="可留空"></label>
        <button class="secondary-button" type="button" data-remove-target="${escapeHtml(target.target_id)}">移除</button>
      </div>
    </article>`;
  }).join("");
  confirmManifestButton.disabled = problemCount > 0 || targets.length === 0;
  manifestHint.textContent = problemCount
    ? `还有 ${problemCount} 个靶标需要修正或移除；字段修改后会自动重新预检。`
    : `全部 ${targets.length} 个靶标均可执行，可以确认并启动批量任务。`;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail || data;
    throw new Error(detail.message || detail.error?.message || JSON.stringify(detail));
  }
  return data;
}

async function reviseManifest(payload) {
  if (!activeManifestBatch?.batch_id) return;
  const batch = await requestJson(`/v1/wf01/tasks/${encodeURIComponent(activeManifestBatch.batch_id)}/targets`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  renderTargetManifest(batch);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function number(value, digits = 2) {
  return value === null || value === undefined ? "—" : Number(value).toFixed(digits);
}

function updateSequenceCount() {
  const sequence = normalizeSequence(sequenceInput.value);
  const valid = /^[ACGTRYSWKMBDHVN]*$/.test(sequence);
  sequenceCount.textContent = `${sequence.length} nt`;
  sequenceMeta.classList.toggle(
    "invalid",
    !valid || (sequence.length > 0 && sequence.length < minimumTemplateLength),
  );
}

function numericValue(id) {
  return Number(document.querySelector(`#${id}`).value);
}

function designParameters() {
  return {
    product_min_bp: numericValue("productMin"),
    product_max_bp: numericValue("productMax"),
    primer_min_size: numericValue("primerSizeMin"),
    primer_opt_size: numericValue("primerSizeOpt"),
    primer_max_size: numericValue("primerSizeMax"),
    primer_min_tm: numericValue("primerTmMin"),
    primer_opt_tm: numericValue("primerTmOpt"),
    primer_max_tm: numericValue("primerTmMax"),
    primer_min_gc: numericValue("primerGcMin"),
    primer_max_gc: numericValue("primerGcMax"),
    probe_min_size: numericValue("probeSizeMin"),
    probe_opt_size: numericValue("probeSizeOpt"),
    probe_max_size: numericValue("probeSizeMax"),
    probe_min_tm: numericValue("probeTmMin"),
    probe_opt_tm: numericValue("probeTmOpt"),
    probe_max_tm: numericValue("probeTmMax"),
  };
}

function setNumberProfile(id, {min, max, value}) {
  const input = document.querySelector(`#${id}`);
  input.min = min;
  input.max = max;
  input.value = value;
}

function updateProbeParameterProfile() {
  const isMgb = probeTypeInput.value === "TAQMAN_MGB";
  const profile = isMgb
    ? {
        probeSizeMin: {min: 13, max: 20, value: 13},
        probeSizeOpt: {min: 13, max: 20, value: 18},
        probeSizeMax: {min: 13, max: 20, value: 20},
        probeTmMin: {min: 50, max: 65, value: 50},
        probeTmOpt: {min: 50, max: 65, value: 57},
        probeTmMax: {min: 50, max: 65, value: 65},
      }
    : {
        probeSizeMin: {min: 18, max: 30, value: 18},
        probeSizeOpt: {min: 18, max: 30, value: 25},
        probeSizeMax: {min: 18, max: 30, value: 30},
        probeTmMin: {min: 50, max: 80, value: 68},
        probeTmOpt: {min: 50, max: 80, value: 70},
        probeTmMax: {min: 50, max: 80, value: 72},
      };
  for (const [id, settings] of Object.entries(profile)) setNumberProfile(id, settings);
  document.querySelector("#probeParameterNote").textContent = isMgb
    ? "MGB 当前显示设计阶段参数（13–20 nt、50–65°C）；程序会另行评估修饰后的有效 Tm。"
    : "显式填写的参数会在标准轮和后续松弛轮中持续生效；候选仍须通过 Agent D 的硬性实验规则。";
}

async function loadToolStatus() {
  toolStatus.innerHTML = '<span class="tool-chip loading">正在检查…</span>';
  try {
    const response = await fetch("/v1/tool-status");
    if (!response.ok) throw new Error("工具状态读取失败");
    const payload = await response.json();
    const tools = [
      ["Primer3", payload.tools?.P1?.available],
      ["MGB", payload.tools?.P3?.available],
      ["Multiplex", payload.tools?.P4?.available],
      ["ViennaRNA", payload.tools?.P6?.available],
      ["BLAST+", payload.tools?.C6?.available],
      ["MAFFT", payload.tools?.C5?.available],
    ];
    toolStatus.innerHTML = tools
      .map(([name, ready]) => `<span class="tool-chip ${ready ? "ready" : "missing"}">${escapeHtml(name)}</span>`)
      .join("");
  } catch (error) {
    toolStatus.innerHTML = `<span class="tool-chip missing">${escapeHtml(error.message)}</span>`;
  }
}

function copyButton(sequence) {
  return `<button class="copy-button" type="button" data-copy="${escapeHtml(sequence)}">复制</button>`;
}

function oligoRow(label, oligo) {
  if (!oligo) return "";
  return `
    <div class="oligo-row">
      <span class="oligo-kind">${label}</span>
      <div>
        <div class="oligo-sequence">${escapeHtml(oligo.sequence_5to3)}</div>
        <div class="oligo-meta">
          <span>${oligo.length_nt} nt</span>
          <span>Tm ${number(oligo.tm_celsius)}°C</span>
          <span>GC ${number(oligo.gc_percent, 1)}%</span>
        </div>
      </div>
      ${copyButton(oligo.sequence_5to3)}
    </div>`;
}

function pillClass(status) {
  if (status === "PASS") return "pass";
  if (status === "FAIL") return "fail";
  return "warn";
}

function candidateCard(candidate) {
  const rules = candidate.rules || [];
  const counts = rules.reduce((summary, rule) => {
    summary[rule.status] = (summary[rule.status] || 0) + 1;
    return summary;
  }, {});
  const specificity = candidate.specificity || {};
  const checks = [
    ["靶标", specificity.target?.verdict || "NOT_CHECKED"],
    ["宿主", specificity.host?.verdict || "NOT_CHECKED"],
    ["近缘种", specificity.cross_species?.verdict || "NOT_CHECKED"],
    ["结构", candidate.amplicon_structure?.status || "NOT_EVALUATED"],
  ];
  return `
    <article class="candidate-card">
      <div class="candidate-head">
        <div>
          <h3>候选 ${candidate.rank ?? "—"} · ${escapeHtml(candidate.verdict)}</h3>
          <p>${escapeHtml(candidate.candidate_id)}</p>
        </div>
        <div class="score">${Math.round((candidate.score || 0) * 100)}<small>/100</small></div>
      </div>
      <div class="oligo-list">
        ${oligoRow("正向", candidate.forward_primer)}
        ${oligoRow("反向", candidate.reverse_primer)}
        ${oligoRow("探针", candidate.probe)}
      </div>
      <div class="candidate-facts">
        <div class="fact"><span>扩增子长度</span><strong>${candidate.amplicon?.length_bp ?? "—"} bp</strong></div>
        <div class="fact"><span>引物 ΔTm</span><strong>${number(Math.abs((candidate.forward_primer?.tm_celsius || 0) - (candidate.reverse_primer?.tm_celsius || 0)))}°C</strong></div>
        <div class="fact"><span>规则统计</span><strong>${counts.PASS || 0} 通过 · ${(counts.WARNING || 0) + (counts.NOT_EVALUATED || 0)} 待关注</strong></div>
      </div>
      <div class="candidate-checks">
        <h4>证据检查</h4>
        <div class="check-grid">
          ${checks.map(([name, status]) => `<span class="check-pill ${pillClass(status)}">${name} · ${escapeHtml(status)}</span>`).join("")}
        </div>
      </div>
    </article>`;
}

function renderResult(data) {
  resultArea.classList.remove("empty");
  emptyState.hidden = true;
  errorState.hidden = true;
  resultContent.hidden = false;
  const warnings = [...new Set([...(data.warnings || []), ...(data.candidates || []).flatMap(item => item.warnings || [])])];
  const statusClass = String(data.status || "").toLowerCase();
  const design = data.input?.design_parameters || {};
  const contextItems = [
    data.target?.target_id ? `靶标 ${data.target.target_id}` : null,
    data.target?.organism ? `目标物种 ${data.target.organism}` : null,
    data.input?.host_species ? `宿主/背景 ${data.input.host_species}` : "宿主/背景 未设置",
    design.product_min_bp !== undefined && design.product_max_bp !== undefined
      ? `扩增子 ${design.product_min_bp}–${design.product_max_bp} bp`
      : null,
    data.input?.probe_type ? `探针 ${data.input.probe_type.replace("TAQMAN_", "")}` : null,
  ].filter(Boolean);
  resultContent.innerHTML = `
    <div class="result-head">
      <div class="result-title">
        <h2>设计结果</h2>
        <p>${escapeHtml(data.assay_id)} · ${data.candidates?.length || 0} 组候选 · Job ${escapeHtml(data.job_id)}</p>
      </div>
      <span class="status-badge ${statusClass}">${escapeHtml(data.status)}</span>
    </div>
    <div class="result-context">${contextItems.map(item => `<span>${escapeHtml(item)}</span>`).join("")}</div>
    ${data.status === "PARTIAL" ? '<div class="result-notice">候选设计已完成，但当前没有 BLAST+/MAFFT 的完整证据。请把这些结果视为待复核候选，而不是实验定稿。</div>' : ""}
    <div class="candidate-list">${(data.candidates || []).map(candidateCard).join("") || "<p>未生成可用候选，请检查错误和输入序列。</p>"}</div>
    ${warnings.length ? `<section class="warnings"><h3>需要关注</h3><ul>${warnings.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>` : ""}
    <details class="raw">
      <summary>查看原始 JSON</summary>
      <pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>
    </details>`;
}

function renderError(message) {
  resultArea.classList.remove("empty");
  emptyState.hidden = true;
  resultContent.hidden = true;
  errorState.hidden = false;
  errorState.innerHTML = `<strong>任务未完成</strong><br>${escapeHtml(message)}`;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const sequence = normalizeSequence(sequenceInput.value);
  if (sequence.length < minimumTemplateLength) {
    renderError(`序列长度为 ${sequence.length} nt，WF-01 至少需要 ${minimumTemplateLength} nt。`);
    sequenceInput.focus();
    return;
  }
  if (!/^[ACGTRYSWKMBDHVN]+$/.test(sequence)) {
    renderError("序列包含无法识别的字符，请仅使用 DNA 碱基和常见 IUPAC 简并码。");
    sequenceInput.focus();
    return;
  }
  const payload = {
    assay_id: document.querySelector("#assayId").value.trim(),
    target_id: document.querySelector("#targetId").value.trim(),
    target_species: document.querySelector("#targetSpecies").value.trim(),
    gene: document.querySelector("#gene").value.trim() || "unspecified",
    sequence,
    host_species: document.querySelector("#hostSpecies").value.trim(),
    probe_type: document.querySelector("#probeType").value,
    design_parameters: designParameters(),
    top_candidates: Number(document.querySelector("#topCandidates").value),
    allow_fallback: document.querySelector("#allowFallback").checked,
  };
  submitButton.disabled = true;
  submitButton.classList.add("busy");
  try {
    const response = await fetch("/v1/wf01/design", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail || data;
      throw new Error(detail.message || detail.error?.message || JSON.stringify(detail));
    }
    renderResult(data);
  } catch (error) {
    renderError(error.message || "本地服务调用失败。");
  } finally {
    submitButton.disabled = false;
    submitButton.classList.remove("busy");
  }
});

sequenceInput.addEventListener("input", updateSequenceCount);
probeTypeInput.addEventListener("change", updateProbeParameterProfile);
document.querySelector("#refreshTools").addEventListener("click", loadToolStatus);
document.querySelector("#loadExample").addEventListener("click", () => {
  sequenceInput.value = exampleSequence;
  document.querySelector("#targetId").value = "fixture_target_001";
  document.querySelector("#targetSpecies").value = "Synthetic test virus";
  document.querySelector("#gene").value = "development_fixture";
  document.querySelector("#hostSpecies").value = "pig";
  document.querySelector("#probeType").value = "TAQMAN_BHQ";
  updateProbeParameterProfile();
  updateSequenceCount();
  sequenceInput.focus();
});

batchFastaInput.addEventListener("input", updateBatchSequenceCount);
document.querySelector("#loadBatchExample").addEventListener("click", () => {
  batchFastaInput.value = `>target_alpha\n${exampleSequence}\n>target_beta\n${exampleSequence.split("").reverse().join("")}`;
  document.querySelector("#batchTargetSpecies").value = "";
  document.querySelector("#batchHostSpecies").value = "";
  document.querySelector("#batchSequenceMode").value = "independent_targets";
  updateBatchSequenceCount();
});

batchPreflightForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = document.querySelector("#batchPreflightButton");
  button.disabled = true;
  const defaults = {};
  const targetSpecies = document.querySelector("#batchTargetSpecies").value.trim();
  const hostSpecies = document.querySelector("#batchHostSpecies").value.trim();
  if (targetSpecies) defaults.target_organism = {name: targetSpecies};
  if (hostSpecies) defaults.hosts = [{name: hostSpecies}];
  const payload = {
    task_name: document.querySelector("#batchTaskName").value.trim(),
    sequence_mode: document.querySelector("#batchSequenceMode").value,
    fasta_text: batchFastaInput.value,
    batch_defaults: defaults,
  };
  try {
    const batch = await requestJson("/v1/wf01/tasks", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    renderTargetManifest(batch);
    manifestPanel.scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    manifestPanel.hidden = false;
    manifestSummary.innerHTML = `<div class="error-state"><strong>预检失败</strong><br>${escapeHtml(error.message)}</div>`;
    manifestTargets.innerHTML = "";
    confirmManifestButton.disabled = true;
  } finally {
    button.disabled = false;
  }
});

manifestTargets.addEventListener("change", async event => {
  const input = event.target.closest("[data-field]");
  const row = event.target.closest("[data-manifest-target]");
  if (!input || !row) return;
  const targetId = row.dataset.manifestTarget;
  const target = (activeManifestBatch?.targets || []).find(item => item.target_id === targetId);
  if (!target) return;
  const changes = {};
  if (input.dataset.field === "sequence") {
    changes.sequence = normalizeSequence(input.value);
  } else if (input.dataset.field === "record_name") {
    changes.record_name = input.value.trim();
  } else if (input.dataset.field === "target_organism") {
    changes.target_organism = input.value.trim() ? {name: input.value.trim()} : null;
  } else {
    changes.hosts = input.value.trim() ? [{name: input.value.trim()}] : [];
  }
  try {
    await reviseManifest({target_changes: [{target_id: targetId, changes}]});
  } catch (error) {
    manifestHint.textContent = error.message;
  }
});

manifestTargets.addEventListener("click", async event => {
  const button = event.target.closest("[data-remove-target]");
  if (!button) return;
  button.disabled = true;
  try {
    await reviseManifest({remove_target_ids: [button.dataset.removeTarget]});
  } catch (error) {
    manifestHint.textContent = error.message;
    button.disabled = false;
  }
});

confirmManifestButton.addEventListener("click", async () => {
  if (!activeManifestBatch?.batch_id) return;
  confirmManifestButton.disabled = true;
  try {
    const result = await requestJson(`/v1/wf01/tasks/${encodeURIComponent(activeManifestBatch.batch_id)}/confirm`, {method: "POST"});
    if (!result.started) {
      manifestHint.textContent = "仍有问题靶标，请先修正或移除。";
      return;
    }
    manifestHint.textContent = `批次 ${result.batch_id} 已确认，正在运行 Primer3 和后续阶段…`;
    const completed = await requestJson(`/v1/wf01/tasks/${encodeURIComponent(result.batch_id)}/run-until-blocked`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}",
    });
    manifestHint.textContent = completed.next_action === "COMPLETE"
      ? "批量任务及报告已经生成。"
      : `批次已推进至 ${completed.status || "当前阶段"}；下一步：${completed.next_action || "等待处理"}。`;
  } catch (error) {
    manifestHint.textContent = error.message;
    confirmManifestButton.disabled = false;
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  await navigator.clipboard.writeText(button.dataset.copy);
  const original = button.textContent;
  button.textContent = "已复制";
  setTimeout(() => { button.textContent = original; }, 1200);
});

updateSequenceCount();
updateBatchSequenceCount();
updateProbeParameterProfile();
loadToolStatus();
