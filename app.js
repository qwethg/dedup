// ─── State ───
let scanResult = null;
let organizeResult = null;
let selectedFiles = new Set();      // dedup selections
let orgSelectedFiles = new Set();   // organize selections
let currentFilter = '';
let orgCurrentFilter = '';
let folderCollapsed = false;
let currentTab = 'dedup';
let hasSend2trash = true;           // from /api/config; false = deletes are permanent
let aiBatches = [];               // AI 勾选批次栈：每次 AI 勾选压入一批，支持多次点击与逐次撤销
let aiFilter = null;                // AI 筛选条件（只影响展示，不改扫描数据）

// ─── API helper：所有 /api/* 请求统一携带本地令牌 ───
const APP_TOKEN = window.__APP_TOKEN__ || '';
function api(url, opts){
  opts = opts || {};
  opts.headers = Object.assign({}, opts.headers, {'X-App-Token': APP_TOKEN});
  return fetch(url, opts);
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ─── Init ───
document.addEventListener('DOMContentLoaded', () => {
  loadConfig();
  loadFolders();
  // Cancel button works for whichever tab is being scanned
  document.getElementById('cancelScanBtn').onclick = cancelCurrentScan;
  // Check previous dedup result (restored from server-side cache after restart)
  api('/api/scan/result').then(r=>r.json()).then(d=>{
    if(d.groups && d.groups.length>=0 && d.total_files>0){
      scanResult = d;
      renderAll();
    }
  });
  // Check previous organize result
  api('/api/organize/result').then(r=>r.json()).then(d=>{
    if(d.categories && d.total_files>0){
      organizeResult = d;
      renderOrganizeAll();
    }
  });
  // Mode select
  const cfgMode = localStorage.getItem('scan_mode');
  if(cfgMode) document.getElementById('modeSelect').value = cfgMode;
});

// ─── Tab Switch ───
function switchTab(tab){
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  if(tab==='dedup'){
    document.querySelectorAll('.tab')[0].classList.add('active');
    document.getElementById('tabDedup').classList.add('active');
  } else {
    document.querySelectorAll('.tab')[1].classList.add('active');
    document.getElementById('tabOrganize').classList.add('active');
  }
  // Update action bar context
  updateActionBar();
}

function loadConfig(){
  api('/api/config').then(r=>r.json()).then(cfg=>{
    document.getElementById('modeSelect').value = cfg.scan_mode || 'exact';
    localStorage.setItem('scan_mode', cfg.scan_mode || 'exact');
    hasSend2trash = cfg.has_send2trash !== false;
    // 首次打开：显示风险声明，确认前不遮挡后续操作但不可跳过
    if(!cfg.disclaimer_accepted){
      document.getElementById('disclaimerModal').classList.add('show');
    }
  });
}

function acceptDisclaimer(){
  api('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({disclaimer_accepted: true})
  }).then(()=>closeModal('disclaimerModal'));
}

function cancelCurrentScan(){
  // 进度浮层为多个任务共用，按当前活动任务分发取消
  if(window._activeProgressJob === 'ai_classify'){
    api('/api/organize/ai_classify/cancel', {method:'POST'}).then(()=>toast('正在取消 AI 分类...'));
    return;
  }
  const url = currentTab==='dedup' ? '/api/scan/cancel' : '/api/organize/cancel';
  api(url, {method:'POST'}).then(()=>toast('正在取消...'));
}

function loadFolders(){
  const el = document.getElementById('folderItems');
  if(el) el.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px">加载中...</div>';
  api('/api/folders').then(r=>r.json()).then(renderFolders);
}

function autodetectFolders(){
  api('/api/folders/autodetect', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    if(d.added && d.added.length){
      toast(`已添加 ${d.added.length} 个文件夹` + (d.skipped.length ? `（${d.skipped.length} 个已存在）` : ''), 'success');
      loadFolders();
    } else if(d.skipped && d.skipped.length){
      toast('发现的文件夹均已在列表中');
    } else {
      toast('未发现微信 / 企业微信文件夹，请手动添加', 'error');
    }
  }).catch(()=>toast('自动扫描失败', 'error'));
}

function renderFolders(folders){
  const el = document.getElementById('folderItems');
  if(!folders.length){
    el.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px">暂无文件夹，添加路径开始</div>';
    return;
  }
  el.innerHTML = folders.map((f,i)=>{
    return `
    <div class="folder-item" data-path="${f.path}">
      <div class="info">
        <span class="label">${f.label}</span>
        ${f.protected?'<span class="protect-badge">保护</span>':''}
        <span class="path" title="${f.path}">${f.path}</span>
        <span class="meta">${f.count} 文件 · ${f.size_str}</span>
      </div>
      <div class="actions">
        <button class="btn btn-sm" data-action="protect" data-idx="${i}">${f.protected?'取消保护':'保护'}</button>
        <button class="btn btn-sm" data-action="remove" data-idx="${i}">移除</button>
      </div>
    </div>`;
  }).join('');
  el.querySelectorAll('button[data-action]').forEach(btn=>{
    btn.addEventListener('click', e=>{
      const item = e.target.closest('.folder-item');
      const path = item.dataset.path;
      const action = e.target.dataset.action;
      if(action==='remove') removeFolder(path);
      else if(action==='protect') toggleProtect(path);
    });
  });
}

function pickFolder(){
  api('/api/folders/pick', {method:'POST'})
    .then(r=>r.json())
    .then(d=>{
      if(d.error) return toast(d.error, 'error');
      if(d.path) document.getElementById('folderPath').value = d.path;
    });
}

function addFolder(){
  const path = document.getElementById('folderPath').value.trim();
  const label = document.getElementById('folderLabel').value.trim();
  if(!path) return toast('请输入路径', 'error');
  api('/api/folders', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path, label, protected:false})
  }).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    document.getElementById('folderPath').value='';
    document.getElementById('folderLabel').value='';
    toast('已添加', 'success');
    loadFolders();
  });
}

function removeFolder(path){
  api('/api/folders?path='+encodeURIComponent(path), {method:'DELETE'})
    .then(r=>r.json()).then(()=>{ loadFolders(); });
}

function toggleProtect(path){
  api('/api/folders/protect', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path})
  }).then(r=>r.json()).then(()=>loadFolders());
}

function toggleFolderPanel(){
  const el = document.getElementById('folderList');
  const toggle = document.getElementById('folderToggle');
  folderCollapsed = !folderCollapsed;
  el.classList.toggle('collapsed', folderCollapsed);
  toggle.textContent = folderCollapsed ? '▸' : '▾';
}

// ══════════════════════════════════════════
// ─── DEDUP: Scan ───
// ══════════════════════════════════════════
function startScan(){
  // 新一次扫描会替换全部结果，旧的 AI 勾选批次随之失效
  aiBatches = [];
  renderAiUnderstanding();
  api('/api/scan', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    document.getElementById('progressTitle').textContent = '扫描重复文件中...';
    document.getElementById('progressOverlay').classList.add('show');
    pollScan();
  });
}

function pollScan(){
  api('/api/scan/status').then(r=>r.json()).then(s=>{
    // total is 0 in single-pass mode -> indeterminate bar, show scanned count
    const fill = document.getElementById('progressFill');
    if(s.total > 0){
      fill.classList.remove('indeterminate');
      fill.style.width = Math.min(100, Math.round(s.progress/s.total*100))+'%';
    } else {
      fill.classList.add('indeterminate');
    }
    document.getElementById('progressText').textContent =
      `已扫描 ${s.progress} 个文件 · ${s.current_path ? s.current_path.split('\\').pop() : ''}`;
    if(s.scanning){
      setTimeout(pollScan, 300);
    } else {
      fill.classList.remove('indeterminate');
      document.getElementById('progressOverlay').classList.remove('show');
      if(s.error){
        toast(s.error, 'error');
      } else if(s.has_result){
        fetchResult();
      } else {
        toast('扫描已取消');
      }
    }
  });
}

function fetchResult(){
  api('/api/scan/result').then(r=>r.json()).then(d=>{
    scanResult = d;
    renderAll();
  });
}

// ─── DEDUP: Render ───
function renderAll(){
  if(!scanResult) return;
  document.getElementById('statsArea').style.display='grid';
  document.getElementById('toolbar').style.display='block';
  document.getElementById('refreshBtn').style.display = '';
  updateStats();

  const exts = {};
  scanResult.groups.forEach(g=>{ exts[g.ext] = (exts[g.ext]||0) + 1; });
  const chipEl = document.getElementById('extChips');
  chipEl.innerHTML = '<span class="chip active" data-ext="" onclick="filterExt(this)">全部</span>' +
    Object.entries(exts).sort((a,b)=>b[1]-a[1]).map(([ext,n])=>
      `<span class="chip" data-ext="${ext}" onclick="filterExt(this)">${ext.replace('.','').toUpperCase()} (${n})</span>`
    ).join('');

  const srcInfo = {};   // source -> {groups: Set, files: n}
  scanResult.groups.forEach((g,gi)=>g.files.forEach(f=>{
    const s = f.source.replace('🔒 ','');
    if(!srcInfo[s]) srcInfo[s] = {groups:new Set(), files:0};
    srcInfo[s].groups.add(gi);
    srcInfo[s].files++;
  }));
  const prevKeep = localStorage.getItem('keep_label') || '';
  document.getElementById('keepLabelSelect').innerHTML =
    '<option value="">保留指定来源...</option>' +
    Object.keys(srcInfo).sort().map(s=>
      `<option value="${escapeHtml(s)}"${s===prevKeep?' selected':''}>` +
      `${escapeHtml(s)}（${srcInfo[s].groups.size} 组 / ${srcInfo[s].files} 个文件）</option>`
    ).join('');

  renderGroups();
}

// 当前筛选条件下实际可见的重复组（搜索框 + 扩展名筛选 + AI 筛选）。
// 渲染和快捷规则 / AI 勾选都必须基于这份列表，避免"看着是一部分、操作的是全部"
function getVisibleGroups(){
  if(!scanResult) return [];
  const search = document.getElementById('searchInput').value.toLowerCase();
  let groups = [...scanResult.groups];
  if(currentFilter) groups = groups.filter(g=>g.ext === currentFilter);
  if(aiFilter) groups = groups.filter(g=>g.files.some(matchAiFilter));
  if(search) groups = groups.filter(g=>g.filename.toLowerCase().includes(search));
  return groups;
}

function renderGroups(){
  if(!scanResult) return;
  const sort = document.getElementById('sortSelect').value;
  let groups = getVisibleGroups();
  if(sort==='savings') groups.sort((a,b)=>b.savings-a.savings);
  else if(sort==='count') groups.sort((a,b)=>b.count-a.count);
  else if(sort==='name') groups.sort((a,b)=>a.filename.localeCompare(b.filename));
  else if(sort==='time') groups.sort((a,b)=>b.files[0].mtime-a.files[0].mtime);

  const el = document.getElementById('groupsArea');
  if(!groups.length){
    el.innerHTML = '<div class="empty"><div class="icon">✓</div><div class="hint">没有发现重复文件</div></div>';
    return;
  }
  el.innerHTML = groups.map(g=>`
    <div class="group-card">
      <div class="group-header">
        <div class="title">
          <span class="fname">${g.filename}</span>
          <span class="badge">${g.ext.replace('.','').toUpperCase()}</span>
          <span class="meta">×${g.count}</span>
          ${g.is_similar?'<span class="similar-tag">可能不同版本</span>':''}
        </div>
        <div class="savings">省 ${g.savings_str}</div>
      </div>
      ${g.files.map((f,i)=>`
        <div class="file-row">
          <input type="checkbox" data-id="${f.id}" data-path="${f.path}" data-size="${f.size}"
            ${selectedFiles.has(f.id)?'checked':''}
            ${f.protected?'style="accent-color:var(--warning)"':''}
            onchange="toggleSelect(this)">
          <span class="f-source">${f.source}</span>
          ${f.protected?'<span class="f-protected"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg></span>':''}
          ${selectedFiles.has(f.id)?'<span class="del-tag">将删除</span>':''}
          <span class="f-path" title="${f.path}">${f.path}</span>
          <span class="f-date">${f.mtime_str}</span>
          <span class="f-size">${formatSize(f.size)}</span>
          <span class="f-actions">
            <button class="f-act-btn" title="打开文件" data-path="${f.path}" data-dir="0" onclick="openPathBtn(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M19 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h6"/></svg></button>
            <button class="f-act-btn" title="打开所在目录" data-path="${f.path}" data-dir="1" onclick="openPathBtn(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></button>
            <button class="f-act-btn f-act-del" title="删除（移至回收站）" data-path="${f.path}" onclick="deleteSingleFile(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></button>
          </span>
        </div>
      `).join('')}
    </div>
  `).join('');
  updateActionBar();
}

function filterExt(el){
  document.querySelectorAll('#extChips .chip').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');
  currentFilter = el.dataset.ext;
  renderGroups();
}

function changeMode(){
  const mode = document.getElementById('modeSelect').value;
  api('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({scan_mode: mode})
  }).then(()=>{
    localStorage.setItem('scan_mode', mode);
    if(scanResult) startScan();
  });
}

// ─── DEDUP: Selection ───
function toggleSelect(cb){
  const id = cb.dataset.id;
  if(cb.checked) selectedFiles.add(id);
  else selectedFiles.delete(id);
  refreshRowTags(cb.closest('.group-card'));
  updateActionBar();
}

// 组内勾选状态变化后，就地更新「将删除」标记：勾选即显示，取消 / 清空即消失
function refreshRowTags(card){
  if(!card) return;
  card.querySelectorAll('.file-row').forEach(r=>{
    const old = r.querySelector('.keep-tag,.del-tag');
    if(old) old.remove();
    if(r.querySelector('input[type=checkbox]').checked){
      r.querySelector('.f-path').insertAdjacentHTML('beforebegin', '<span class="del-tag">将删除</span>');
    }
  });
}

// ─── Open file / reveal in Explorer ───
function openPathBtn(btn){
  api('/api/open', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path: btn.dataset.path, dir: btn.dataset.dir === '1'})
  }).then(r=>r.json()).then(d=>{
    if(d.error) toast(d.error, 'error');
  }).catch(()=>toast('打开失败', 'error'));
}

// 行内单文件删除（去重页浮动按钮）：统一样式的确认弹窗，移至回收站后从界面移除
let _singleDeletePath = null;
function deleteSingleFile(btn){
  _singleDeletePath = btn.dataset.path;
  document.getElementById('singleDeletePath').textContent = _singleDeletePath;
  document.getElementById('singleDeleteModal').classList.add('show');
}
function confirmSingleDelete(){
  const path = _singleDeletePath;
  _singleDeletePath = null;
  closeModal('singleDeleteModal');
  if(!path) return;
  runBatchedOperate('delete', [{dest:null, paths:[path]}], (donePaths)=>{
    if(currentTab==='dedup') removeFilesFromUI(donePaths);
    else removeOrgFilesFromUI(donePaths);
  });
}

function clearSelections(){
  if(currentTab==='dedup'){
    selectedFiles.clear();
    aiBatches = [];
    renderAiUnderstanding();
    document.querySelectorAll('#tabDedup .file-row input[type=checkbox]').forEach(cb=>cb.checked=false);
    document.querySelectorAll('#tabDedup .group-card').forEach(refreshRowTags);
  } else {
    orgSelectedFiles.clear();
    // 重新渲染，分类标题的勾选框和「已选 N」徽标一并复位
    renderCategories();
  }
  updateActionBar();
}

function updateActionBar(){
  const count = currentTab==='dedup' ? selectedFiles.size : orgSelectedFiles.size;
  const bar = document.getElementById('actionBar');
  if(count > 0){
    bar.classList.add('show');
    document.getElementById('selCount').textContent = count;
    let size = 0;
    if(currentTab==='dedup' && scanResult){
      scanResult.groups.forEach(g=>g.files.forEach(f=>{ if(selectedFiles.has(f.id)) size += f.size; }));
    } else if(currentTab==='organize' && organizeResult){
      organizeResult.categories.forEach(c=>c.files.forEach(f=>{ if(orgSelectedFiles.has(f.id)) size += f.size; }));
    }
    document.getElementById('selSize').textContent = '· ' + formatSize(size);
  } else {
    bar.classList.remove('show');
  }
}

// ─── DEDUP: Quick Rules ───
function applyQuickRule(rule){
  if(!scanResult) return;
  const label = document.getElementById('keepLabelSelect').value;
  if(rule==='keep_label'){
    if(!label) return toast('请先在下拉框中选择要保留的来源', 'error');
    localStorage.setItem('keep_label', label);
  }
  const visibleGroups = getVisibleGroups();
  if(!visibleGroups.length) return toast('当前筛选条件下没有重复组', 'error');
  api('/api/quick_rule', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({rule, groups: visibleGroups, keep_label: label})
  }).then(r=>r.json()).then(d=>{
    selectedFiles.clear();
    d.selections.forEach(id=>selectedFiles.add(id));
    renderGroups();
    const scoped = visibleGroups.length < scanResult.groups.length
      ? `（仅作用于筛选出的 ${visibleGroups.length} 组）` : '';
    if(rule==='keep_label'){
      const skipped = d.groups_skipped || 0;
      if(!d.selections.length){
        // 0 勾选是最容易被误解为"功能坏了"的情况，给出明确原因
        toast(skipped > 0
          ? `所有重复组里都没有来源「${label}」的文件，未勾选任何文件`
          : `各重复组均只包含「${label}」的文件，没有可清理的副本`, 'error');
      } else {
        toast(`已勾选 ${d.selections.length} 个待删文件（保留「${label}」）` +
          (skipped > 0 ? `；${skipped} 个组不含该来源，已整组跳过` : '') + scoped, 'success');
      }
    } else {
      const kept = {keep_newest:'每组保留最新的 1 个', keep_oldest:'每组保留最旧的 1 个'}[rule] || '';
      toast(`已勾选 ${d.selections.length} 个待删文件（${kept}）${scoped}`, 'success');
    }
  });
}

// 下拉框选中来源后立即应用（"应用"按钮仍可用于改动勾选后重新应用）
function applyKeepSource(){
  const label = document.getElementById('keepLabelSelect').value;
  if(label) applyQuickRule('keep_label');
}

// ─── DEDUP: AI ───
// 把后端返回的规则 DSL 转成人类可读描述
function ruleToText(r){
  if(!r || typeof r !== 'object') return String(r);
  const scope = (r.scope && Object.keys(r.scope).length)
    ? '（范围: ' + Object.entries(r.scope).map(([k,v])=>k+'='+v).join(', ') + '）' : '';
  switch(r.type){
    case 'keep_source': return `保留来源含「${r.value}」的，其余勾选` + scope;
    case 'keep_newest': return '每组保留最新的，其余勾选' + scope;
    case 'keep_oldest': return '每组保留最旧的，其余勾选' + scope;
    case 'keep_shortest_path': return '每组保留路径最短的，其余勾选' + scope;
    case 'keep_longest_path': return '每组保留路径最长的，其余勾选' + scope;
    case 'keep_largest': return '每组保留最大的，其余勾选' + scope;
    case 'keep_smallest': return '每组保留最小的，其余勾选' + scope;
    case 'select_where': return `勾选 ${r.field} ${r.op} ${r.value} 的文件` + scope;
    default: return JSON.stringify(r);
  }
}

function applyAI(){
  const text = document.getElementById('aiInput').value.trim();
  if(!text) return;
  if(!scanResult) return toast('请先扫描', 'error');
  const visibleGroups = getVisibleGroups();
  if(!visibleGroups.length) return toast('当前筛选条件下没有重复组', 'error');
  const btn = document.getElementById('aiBtn');
  btn.disabled = true;
  btn.textContent = '分析中...';
  api('/api/ai', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text, groups: visibleGroups})
  }).then(r=>r.json()).then(d=>{
    btn.disabled = false;
    btn.textContent = 'AI 勾选';
    if(d.error) return toast(d.error, 'error');
    // 并入现有勾选，不清空手动勾选；每次 AI 勾选作为一批压栈，可多次点击、逐批撤销
    const batch = {
      ids: new Set(d.selections || []),
      understanding: d.understanding || '(无说明)',
      rules: d.rules || [],
      groupsAffected: d.groups_affected || 0,
      truncated: !!d.truncated,
      totalGroups: d.total_groups,
      validation: d.validation || {}
    };
    batch.ids.forEach(id=>selectedFiles.add(id));
    aiBatches.push(batch);
    renderGroups();
    renderAiUnderstanding();
    toast(`AI 勾选了 ${batch.ids.size} 个文件，请确认后操作`, 'success');
  }).catch(e=>{
    btn.disabled = false;
    btn.textContent = 'AI 勾选';
    toast('AI请求失败: '+e.message, 'error');
  });
}

// AI 理解区渲染：始终展示最新一批的理解与规则，并给出累计统计和撤销入口
function renderAiUnderstanding(){
  const ue = document.getElementById('aiUnderstanding');
  if(!aiBatches.length){ ue.style.display = 'none'; ue.innerHTML = ''; return; }
  const b = aiBatches[aiBatches.length-1];
  let html = `<div><b>AI 理解：</b>${escapeHtml(b.understanding)}</div>`;
  if(b.rules.length){
    html += '<div style="margin-top:4px"><b>规则：</b>' +
      b.rules.map(r=>`<div>· ${escapeHtml(ruleToText(r))}</div>`).join('') + '</div>';
  }
  html += `<div style="margin-top:4px">本次影响 ${b.groupsAffected} 个重复组，勾选 ${b.ids.size} 个文件` +
    (b.truncated ? `（共 ${b.totalGroups} 组，规模保护仅处理了前一部分，可分次操作）` : '') + '</div>';
  const v = b.validation;
  const fixes = [];
  if(v.dropped_invalid) fixes.push(`忽略无效 id ${v.dropped_invalid} 个`);
  if(v.dropped_protected) fixes.push(`跳过受保护文件 ${v.dropped_protected} 个`);
  if(v.kept_per_group) fixes.push(`${v.kept_per_group} 个组原本会被全选，已保底保留 1 个`);
  if(fixes.length) html += `<div style="margin-top:4px;color:var(--warning)">校验修正：${fixes.join('；')}</div>`;
  if(aiBatches.length > 1){
    const total = aiBatches.reduce((s,x)=>s+x.ids.size, 0);
    html += `<div style="margin-top:4px;color:var(--text-dim)">已累计 ${aiBatches.length} 次 AI 勾选，共 ${total} 个文件</div>`;
  }
  html += `<div style="margin-top:6px;display:flex;gap:8px">` +
    `<button class="btn btn-sm" onclick="undoAI()">撤销本次 AI 勾选</button>` +
    (aiBatches.length > 1 ? `<button class="btn btn-sm" onclick="undoAIAll()">撤销全部 AI 勾选</button>` : '') +
    `</div>`;
  ue.innerHTML = html;
  ue.style.display = 'block';
}

function undoAI(){
  const b = aiBatches.pop();
  if(!b) return;
  // 只移除不再被其他批次引用的 id，避免误撤之前批次的勾选
  b.ids.forEach(id=>{ if(!aiBatches.some(x=>x.ids.has(id))) selectedFiles.delete(id); });
  renderAiUnderstanding();
  renderGroups();
  toast('已撤销本次 AI 勾选');
}

function undoAIAll(){
  aiBatches.forEach(b=>b.ids.forEach(id=>selectedFiles.delete(id)));
  aiBatches = [];
  renderAiUnderstanding();
  renderGroups();
  toast('已撤销全部 AI 勾选');
}

// ─── DEDUP: AI 筛选（只过滤展示，不改扫描数据和勾选） ───
function matchAiFilter(f){
  if(!aiFilter) return true;
  if(aiFilter.ext && f.ext !== aiFilter.ext) return false;
  if(aiFilter.size_gt_mb != null && f.size <= aiFilter.size_gt_mb*1024*1024) return false;
  if(aiFilter.size_lt_mb != null && f.size >= aiFilter.size_lt_mb*1024*1024) return false;
  if(aiFilter.date_after && f.mtime <= Date.parse(aiFilter.date_after)/1000) return false;
  if(aiFilter.date_before && f.mtime >= Date.parse(aiFilter.date_before)/1000) return false;
  if(aiFilter.name_contains && !f.name.toLowerCase().includes(String(aiFilter.name_contains).toLowerCase())) return false;
  return true;
}

function applyAIFilter(){
  const text = document.getElementById('aiInput').value.trim();
  if(!text) return;
  if(!scanResult) return toast('请先扫描', 'error');
  const btn = document.getElementById('aiFilterBtn');
  btn.disabled = true;
  btn.textContent = '筛选中...';
  api('/api/ai/filter', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text})
  }).then(r=>r.json()).then(d=>{
    btn.disabled = false;
    btn.textContent = '筛选';
    if(d.error) return toast(d.error, 'error');
    aiFilter = d.filter || {};
    renderGroups();
    const ue = document.getElementById('aiUnderstanding');
    ue.style.display = 'block';
    ue.innerHTML = `<div><b>AI 筛选：</b>${escapeHtml(d.understanding || '')}</div>` +
      `<div style="margin-top:4px">条件：<code>${escapeHtml(JSON.stringify(aiFilter))}</code></div>` +
      `<div style="margin-top:6px"><button class="btn btn-sm" onclick="clearAIFilter()">清除筛选</button></div>`;
  }).catch(e=>{
    btn.disabled = false;
    btn.textContent = '筛选';
    toast('AI 筛选失败: '+e.message, 'error');
  });
}

function clearAIFilter(){
  aiFilter = null;
  document.getElementById('aiUnderstanding').style.display = 'none';
  renderGroups();
}

// ══════════════════════════════════════════
// ─── ORGANIZE: Scan ───
// ══════════════════════════════════════════
function startOrganizeScan(){
  api('/api/organize/scan', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    document.getElementById('progressTitle').textContent = '整理扫描中...';
    document.getElementById('progressOverlay').classList.add('show');
    pollOrganizeScan();
  });
}

function pollOrganizeScan(){
  api('/api/organize/status').then(r=>r.json()).then(s=>{
    const fill = document.getElementById('progressFill');
    if(s.total > 0){
      fill.classList.remove('indeterminate');
      fill.style.width = Math.min(100, Math.round(s.progress/s.total*100))+'%';
    } else {
      fill.classList.add('indeterminate');
    }
    document.getElementById('progressText').textContent =
      `已扫描 ${s.progress} 个文件 · ${s.current_path ? s.current_path.split('\\').pop() : ''}`;
    if(s.scanning){
      setTimeout(pollOrganizeScan, 300);
    } else {
      fill.classList.remove('indeterminate');
      document.getElementById('progressOverlay').classList.remove('show');
      if(s.error){
        toast(s.error, 'error');
      } else if(s.has_result){
        fetchOrganizeResult();
      } else {
        toast('扫描已取消');
      }
    }
  });
}

function fetchOrganizeResult(){
  api('/api/organize/result').then(r=>r.json()).then(d=>{
    organizeResult = d;
    renderOrganizeAll();
  });
}

// ─── ORGANIZE: Render ───
function renderOrganizeAll(){
  if(!organizeResult) return;
  document.getElementById('orgStatsArea').style.display='grid';
  document.getElementById('orgToolbar').style.display='block';
  document.getElementById('orgRefreshBtn').style.display = '';
  // Update stats
  document.getElementById('orgStatTotal').textContent = organizeResult.total_files;
  document.getElementById('orgStatCats').textContent = organizeResult.total_categories;
  let totalSize = 0;
  organizeResult.categories.forEach(c=>totalSize += c.total_size);
  document.getElementById('orgStatSize').textContent = formatSize(totalSize);
  document.getElementById('orgStatSelected').textContent = orgSelectedFiles.size;

  // Ext chips
  const exts = {};
  organizeResult.categories.forEach(c=>c.files.forEach(f=>{ exts[f.ext] = (exts[f.ext]||0) + 1; }));
  const chipEl = document.getElementById('orgExtChips');
  chipEl.innerHTML = '<span class="chip active" data-ext="" onclick="orgFilterExt(this)">全部</span>' +
    Object.entries(exts).sort((a,b)=>b[1]-a[1]).map(([ext,n])=>
      `<span class="chip" data-ext="${ext}" onclick="orgFilterExt(this)">${ext.replace('.','').toUpperCase()} (${n})</span>`
    ).join('');

  renderCategories();
}

// 当前筛选条件下实际可见的分类（搜索框 + 扩展名筛选）。
// 渲染、分类全选、全局全选都必须基于这份列表
function getVisibleCategories(){
  if(!organizeResult) return [];
  const search = document.getElementById('orgSearchInput').value.toLowerCase();
  let categories = [...organizeResult.categories];
  if(orgCurrentFilter){
    categories = categories.map(c=>({...c, files:c.files.filter(f=>f.ext===orgCurrentFilter)}))
                           .filter(c=>c.files.length > 0);
  }
  if(search){
    categories = categories.map(c=>({...c, files:c.files.filter(f=>f.name.toLowerCase().includes(search))}))
                           .filter(c=>c.files.length > 0);
  }
  return categories;
}

function renderCategories(){
  if(!organizeResult) return;
  const categories = getVisibleCategories();

  const el = document.getElementById('categoriesArea');
  if(!categories.length){
    el.innerHTML = '<div class="empty"><div class="icon">□</div><div class="hint">没有找到文件</div></div>';
    return;
  }

  el.innerHTML = categories.map((c,ci)=>{
    const allIds = c.files.filter(f=>!f.protected).map(f=>f.id);
    const selCount = allIds.filter(id=>orgSelectedFiles.has(id)).length;
    const allSelected = allIds.length > 0 && selCount === allIds.length;
    return `
    <div class="category-card">
      <div class="category-header" onclick="toggleCategoryBody(${ci})">
        <div class="title" style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" style="width:18px;height:18px;cursor:pointer;accent-color:var(--accent)"
            ${allSelected?'checked':''}
            onchange="toggleCategorySelect(${ci}, this.checked)"
            onclick="event.stopPropagation()">
          <span class="cname">${c.category}</span>
          <span class="badge">${c.count} 个文件</span>
          ${selCount>0?`<span class="badge" style="background:rgba(78,138,255,.15);color:var(--accent)">已选 ${selCount}</span>`:''}
        </div>
        <div class="size">${c.total_size_str}</div>
      </div>
      <div class="category-body" id="catBody_${ci}">
        ${c.files.map(f=>`
          <div class="file-row">
            <input type="checkbox" data-id="${f.id}" data-path="${f.path}" data-size="${f.size}"
              ${orgSelectedFiles.has(f.id)?'checked':''}
              ${f.protected?'style="accent-color:var(--warning)"':''}
              onchange="toggleOrgSelect(this)">
            <span class="f-source">${f.source}</span>
            ${f.protected?'<span class="f-protected"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg></span>':''}
            <span class="f-path" title="${f.path}">${f.path}</span>
            <span class="f-date">${f.mtime_str}</span>
            <span class="f-size">${formatSize(f.size)}</span>
            <span class="f-actions">
              <button class="f-act-btn" title="打开文件" data-path="${f.path}" data-dir="0" onclick="openPathBtn(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M19 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h6"/></svg></button>
              <button class="f-act-btn" title="打开所在目录" data-path="${f.path}" data-dir="1" onclick="openPathBtn(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></button>
              <button class="f-act-btn f-act-del" title="删除（移至回收站）" data-path="${f.path}" onclick="deleteSingleFile(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></button>
            </span>
          </div>
        `).join('')}
      </div>
    </div>`;
  }).join('');

  // Auto-expand first category
  if(categories.length > 0 && !document.getElementById('catBody_0').classList.contains('expanded')){
    // Keep collapsed by default, user clicks to expand
  }
  updateOrgStats();
}

function toggleCategoryBody(idx){
  const el = document.getElementById('catBody_'+idx);
  if(el) el.classList.toggle('expanded');
}

function toggleCategorySelect(ci, checked){
  const cat = getVisibleCategories()[ci];
  if(!cat) return;
  if(checked){
    cat.files.forEach(f=>{ if(!f.protected) orgSelectedFiles.add(f.id); });
  } else {
    cat.files.forEach(f=>orgSelectedFiles.delete(f.id));
  }
  renderCategories();
  updateActionBar();
}

function orgFilterExt(el){
  document.querySelectorAll('#orgExtChips .chip').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');
  orgCurrentFilter = el.dataset.ext;
  renderCategories();
}

function toggleOrgSelect(cb){
  const id = cb.dataset.id;
  if(cb.checked) orgSelectedFiles.add(id);
  else orgSelectedFiles.delete(id);
  updateOrgStats();
  updateActionBar();
}

function updateOrgStats(){
  document.getElementById('orgStatSelected').textContent = orgSelectedFiles.size;
}

function orgSelectAll(){
  if(!organizeResult) return;
  const visible = getVisibleCategories();
  visible.forEach(c=>c.files.forEach(f=>{
    if(!f.protected) orgSelectedFiles.add(f.id);
  }));
  renderCategories();
  updateActionBar();
  const scoped = visible.length < organizeResult.categories.length
    ? `（仅作用于筛选出的 ${visible.length} 个分类）` : '';
  toast(`已选 ${orgSelectedFiles.size} 个文件${scoped}`, 'success');
}

function orgSelectNone(){
  orgSelectedFiles.clear();
  renderCategories();
  updateActionBar();
}

// ─── ORGANIZE: AI 智能分类（后台任务 + 进度轮询） ───
function aiClassifyOthers(){
  if(!organizeResult) return toast('请先扫描', 'error');
  const others = organizeResult.categories.find(c=>c.category==='其他');
  if(!others || !others.files.length) return toast('没有「其他」类文件', 'error');
  const btn = document.getElementById('aiClassifyBtn');
  btn.disabled = true;
  btn.textContent = 'AI 分类中...';
  api('/api/organize/ai_classify', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({})
  }).then(r=>r.json()).then(d=>{
    if(d.error){
      btn.disabled = false;
      btn.textContent = 'AI 智能分类';
      return toast(d.error, 'error');
    }
    // 任务已启动：显示进度浮层并开始轮询
    window._activeProgressJob = 'ai_classify';
    document.getElementById('progressTitle').textContent = 'AI 智能分类中...';
    document.getElementById('cancelScanBtn').textContent = '取消分类';
    document.getElementById('forceStopAiBtn').style.display = '';
    document.getElementById('progressOverlay').classList.add('show');
    pollAiClassify();
  }).catch(e=>{
    btn.disabled = false;
    btn.textContent = 'AI 智能分类';
    toast('AI 分类启动失败: '+e.message, 'error');
  });
}

function pollAiClassify(){
  api('/api/organize/ai_classify/status').then(r=>r.json()).then(s=>{
    const fill = document.getElementById('progressFill');
    if(s.total_batches > 0){
      fill.classList.remove('indeterminate');
      fill.style.width = Math.min(100, Math.round(s.done_batches/s.total_batches*100))+'%';
    } else {
      fill.classList.add('indeterminate');
    }
    document.getElementById('progressText').textContent =
      `第 ${s.done_batches}/${s.total_batches} 批 · 已分类 ${s.classified}/${s.total_others} 个文件` +
      (s.failed_batches ? ` · ${s.failed_batches} 批失败` : '');
    if(s.running){
      setTimeout(pollAiClassify, 800);
      return;
    }
    // 结束：恢复浮层与按钮状态
    fill.classList.remove('indeterminate');
    document.getElementById('progressOverlay').classList.remove('show');
    document.getElementById('cancelScanBtn').textContent = '取消扫描';
    document.getElementById('forceStopAiBtn').style.display = 'none';
    window._activeProgressJob = null;
    const btn = document.getElementById('aiClassifyBtn');
    btn.disabled = false;
    btn.textContent = 'AI 智能分类';
    if(s.error){
      toast('AI 分类失败: '+s.error, 'error');
    } else if(s.has_result){
      fetchOrganizeResult();
      toast(`AI 分类完成：${s.classified}/${s.total_others} 个文件` +
            (s.failed_batches ? `（${s.failed_batches} 批失败）` : ''),
            s.failed_batches ? 'error' : 'success');
    }
  }).catch(()=>{ setTimeout(pollAiClassify, 1500); });
}

function forceStopAiClassify(){
  api('/api/organize/ai_classify/force_stop', {method:'POST'})
    .then(()=>toast('已强行停止 AI 分类（已完成的批次结果保留）'));
}

// ══════════════════════════════════════════
// ─── Delete / Move (shared) ───
// ══════════════════════════════════════════
function getSelectedPaths(){
  const paths = [];
  if(currentTab==='dedup' && scanResult){
    scanResult.groups.forEach(g=>g.files.forEach(f=>{
      if(selectedFiles.has(f.id)) paths.push(f.path);
    }));
  } else if(currentTab==='organize' && organizeResult){
    organizeResult.categories.forEach(c=>c.files.forEach(f=>{
      if(orgSelectedFiles.has(f.id)) paths.push(f.path);
    }));
  }
  return paths;
}

function confirmDelete(){
  const paths = getSelectedPaths();
  document.getElementById('deleteDesc').innerHTML = hasSend2trash
    ? `将删除 <span id="deleteCount" style="color:var(--danger);font-weight:600">${paths.length}</span> 个文件到回收站，可恢复。确认继续？`
    : `注意：当前环境不支持回收站，<span id="deleteCount" style="color:var(--danger);font-weight:600">${paths.length}</span> 个文件将被<b>永久删除，不可恢复</b>！确认继续？`;
  document.getElementById('deleteModal').classList.add('show');
}

// ─── Batched operate (delete / move) ───
const OP_BATCH = 50;   // files per HTTP request — keeps progress without 1-request-per-file

function runBatchedOperate(action, jobs, onDone){
  // jobs: [{dest, paths[]}]  (dest = null for delete)
  const totalCount = jobs.reduce((s,j)=>s+j.paths.length, 0);
  const verb = action==='delete' ? '删除' : '移动';
  showOpProgress(`正在${verb}...`, totalCount);
  let jobIdx = 0, done = 0, success = 0, failed = 0;
  const donePaths = [];
  function step(){
    if(jobIdx >= jobs.length){
      hideOpProgress();
      toast(`已${verb} ${success} 个文件${failed>0?'，失败 '+failed+' 个':''}`, failed>0?'error':'success');
      onDone(donePaths);
      clearSelections();
      return;
    }
    const job = jobs[jobIdx];
    const batch = job.paths.splice(0, OP_BATCH);
    updateOpProgress(done, totalCount, batch[0]);
    const body = {action, files: batch};
    if(job.dest) body.dest_dir = job.dest;
    api('/api/operate', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    }).then(r=>r.json()).then(d=>{
      if(d.ok && d.results){
        d.results.forEach(r=>{ if(r.ok){ success++; donePaths.push(r.path); } else failed++; });
      } else {
        failed += batch.length;
      }
      done += batch.length;
      if(!job.paths.length) jobIdx++;
      setTimeout(step, 10);
    }).catch(()=>{
      failed += batch.length; done += batch.length;
      if(!job.paths.length) jobIdx++;
      setTimeout(step, 10);
    });
  }
  step();
}

function executeDelete(){
  const paths = getSelectedPaths();
  closeModal('deleteModal');
  runBatchedOperate('delete', [{dest:null, paths}], (donePaths)=>{
    if(currentTab==='dedup') removeFilesFromUI(donePaths);
    else removeOrgFilesFromUI(donePaths);
  });
}

function showMoveModal(){
  const paths = getSelectedPaths();
  document.getElementById('moveCount').textContent = paths.length;
  document.getElementById('moveModal').classList.add('show');
}

function pickMoveDest(){
  api('/api/folders/pick', {method:'POST'})
    .then(r=>r.json())
    .then(d=>{
      if(d.error) return toast(d.error, 'error');
      if(d.path) document.getElementById('moveDestInput').value = d.path;
    });
}

function executeMove(){
  const paths = getSelectedPaths();
  const dest = document.getElementById('moveDestInput').value.trim();
  if(!dest) return toast('请输入目标路径', 'error');
  closeModal('moveModal');
  runBatchedOperate('move', [{dest, paths}], (donePaths)=>{
    if(currentTab==='dedup') removeFilesFromUI(donePaths);
    else removeOrgFilesFromUI(donePaths);
  });
}

// ─── Archive by category (organize tab) ───
function showArchiveModal(){
  if(!organizeResult) return;
  let count = 0;
  organizeResult.categories.forEach(c=>c.files.forEach(f=>{
    if(f.protected) return;
    if(orgSelectedFiles.size === 0 || orgSelectedFiles.has(f.id)) count++;
  }));
  if(!count) return toast('没有可归档的文件', 'error');
  document.getElementById('archiveCount').textContent = count;
  document.getElementById('archiveModal').classList.add('show');
}

function pickArchiveDest(){
  api('/api/folders/pick', {method:'POST'})
    .then(r=>r.json())
    .then(d=>{
      if(d.error) return toast(d.error, 'error');
      if(d.path) document.getElementById('archiveDestInput').value = d.path;
    });
}

function executeArchive(){
  const root = document.getElementById('archiveDestInput').value.trim();
  if(!root) return toast('请输入目标根目录', 'error');
  closeModal('archiveModal');
  const cleanRoot = root.replace(/[\\/]+$/,'');
  // One job per category: move its files to <root>/<category>/
  const jobs = [];
  organizeResult.categories.forEach(c=>{
    const paths = c.files
      .filter(f=>!f.protected && (orgSelectedFiles.size===0 || orgSelectedFiles.has(f.id)))
      .map(f=>f.path);
    if(paths.length) jobs.push({dest: cleanRoot + '/' + c.category, paths});
  });
  runBatchedOperate('move', jobs, (donePaths)=>removeOrgFilesFromUI(donePaths));
}

// ─── Remove operated files from UI ───
function removeFilesFromUI(donePaths){
  if(!donePaths.length || !scanResult) return;
  const doneSet = new Set(donePaths);
  scanResult.groups.forEach(g=>{
    g.files = g.files.filter(f=>!doneSet.has(f.path));
    // Sync per-group count/savings with the remaining files
    g.count = g.files.length;
    g.savings = g.files.reduce((s,f)=>s+f.size, 0) - (g.files[0] ? g.files[0].size : 0);
    g.savings_str = formatSize(g.savings);
  });
  scanResult.groups = scanResult.groups.filter(g=>g.files.length > 1);
  updateStats();
  renderGroups();
  document.getElementById('refreshBtn').style.display = '';
}

function removeOrgFilesFromUI(donePaths){
  if(!donePaths.length || !organizeResult) return;
  const doneSet = new Set(donePaths);
  organizeResult.categories.forEach(c=>{
    c.files = c.files.filter(f=>!doneSet.has(f.path));
    // Sync per-category count/size with the remaining files
    c.count = c.files.length;
    c.total_size = c.files.reduce((s,f)=>s+f.size, 0);
    c.total_size_str = formatSize(c.total_size);
  });
  organizeResult.categories = organizeResult.categories.filter(c=>c.files.length > 0);
  // Recalculate totals
  organizeResult.total_files = organizeResult.categories.reduce((s,c)=>s+c.files.length, 0);
  organizeResult.total_categories = organizeResult.categories.length;
  renderOrganizeAll();
  document.getElementById('orgRefreshBtn').style.display = '';
}

function updateStats(){
  if(!scanResult) return;
  scanResult.total_dup_files = scanResult.groups.reduce((s,g)=>s+g.files.length, 0);
  scanResult.total_groups = scanResult.groups.length;
  let totalSavings = 0;
  scanResult.groups.forEach(g=>{
    // files[0] is the oldest copy; savings is estimated against keeping it
    totalSavings += g.files.reduce((s,f)=>s+f.size, 0) - g.files[0].size;
  });
  scanResult.total_savings = totalSavings;
  scanResult.total_savings_str = formatSize(totalSavings);
  document.getElementById('statTotal').textContent = scanResult.total_files;
  document.getElementById('statGroups').textContent = scanResult.total_groups;
  document.getElementById('statDupFiles').textContent = scanResult.total_dup_files;
  document.getElementById('statSavings').textContent = scanResult.total_savings_str;
}

// ─── Export ───
function toggleExportMenu(e){
  e.stopPropagation();
  document.getElementById('exportMenu').classList.toggle('show');
}
// 点击页面其他位置时收起导出菜单
document.addEventListener('click', ()=>{
  const m = document.getElementById('exportMenu');
  if(m) m.classList.remove('show');
});

function exportData(fmt){
  const m = document.getElementById('exportMenu');
  if(m) m.classList.remove('show');
  // window.open 无法带请求头，/api/export 允许用查询参数传令牌
  window.open('/api/export?format='+fmt+'&token='+encodeURIComponent(APP_TOKEN));
}

// ─── Log ───
function showLog(){
  api('/api/log').then(r=>r.text()).then(t=>{
    document.getElementById('logContent').textContent = t || '(空)';
    document.getElementById('logModal').classList.add('show');
  });
}

// ─── Help ───
function showHelp(){
  document.getElementById('helpModal').classList.add('show');
}

// 清空日志：两段式确认（第一次点击变为"确认清空？"，3 秒内再点执行）
function clearLog(btn){
  if(!btn.dataset.armed){
    btn.dataset.armed = '1';
    btn.textContent = '确认清空？';
    setTimeout(()=>{ btn.dataset.armed = ''; btn.textContent = '清空'; }, 3000);
    return;
  }
  btn.dataset.armed = '';
  btn.textContent = '清空';
  api('/api/log', {method:'DELETE'}).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    toast('日志已清空', 'success');
    // 重新拉取：服务端会留下一条 LOG_CLEAR 记录，界面与实际内容保持一致
    return api('/api/log').then(r=>r.text()).then(t=>{
      document.getElementById('logContent').textContent = t || '(空)';
    });
  }).catch(()=>toast('清空失败', 'error'));
}

// ─── Settings ───
let fileTypeDefaults = [];   // built-in default ext list from backend

function fillDefaultFileTypes(){
  document.getElementById('setFileTypes').value = fileTypeDefaults.join(', ');
}

function showSettings(){
  api('/api/config').then(r=>r.json()).then(cfg=>{
    document.getElementById('setApiKey').value = cfg.api_key === '***' ? '' : (cfg.api_key||'');
    document.getElementById('setApiBase').value = cfg.api_base || 'https://api.openai.com/v1';
    document.getElementById('setApiModel').value = cfg.api_model || 'gpt-4o-mini';
    document.getElementById('setAiSendPaths').checked = !!cfg.ai_send_paths;
    document.getElementById('aiTestResult').textContent = '';
    document.getElementById('setMinSize').value = cfg.min_size_kb || 1;
    // File types: array = custom list; "all" = no filter (empty box); "work" = default preset
    fileTypeDefaults = cfg.file_type_defaults || [];
    const ft = cfg.file_types;
    if (Array.isArray(ft)) {
      document.getElementById('setFileTypes').value = ft.join(', ');
    } else if (ft === 'all') {
      document.getElementById('setFileTypes').value = '';
    } else {
      document.getElementById('setFileTypes').value = fileTypeDefaults.join(', ');
    }
    // Load keywords
    renderKeywordRows(cfg.organize_keywords || {});
    document.getElementById('settingsModal').classList.add('show');
  });
}

function closeSettings(){
  document.getElementById('settingsModal').classList.remove('show');
}

// ─── 设置: AI 连接测试（用面板中当前填的值，不必先保存） ───
function testAIConnection(){
  const el = document.getElementById('aiTestResult');
  el.style.color = 'var(--text-dim)';
  el.textContent = '测试中...';
  api('/api/ai/test', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      api_key: document.getElementById('setApiKey').value.trim(),
      api_base: document.getElementById('setApiBase').value.trim(),
      api_model: document.getElementById('setApiModel').value.trim(),
    })
  }).then(r=>r.json()).then(d=>{
    if(d.ok){
      el.style.color = 'var(--accent)';
      el.textContent = `连接成功（${d.model}），配置已自动保存`;
      // 保存成功后把 key 输入框还原为掩码态，与"已保存"语义一致
      const keyInput = document.getElementById('setApiKey');
      if(keyInput.value.trim()){ keyInput.value = ''; keyInput.placeholder = '已保存（输入新 Key 可更换）'; }
    } else {
      el.style.color = 'var(--danger)';
      el.textContent = '连接失败：' + (d.error || '未知错误');
    }
  }).catch(e=>{
    el.style.color = 'var(--danger)';
    el.textContent = '连接失败：' + e.message;
  });
}

function renderKeywordRows(keywords){
  const el = document.getElementById('keywordRows');
  el.innerHTML = Object.entries(keywords).map(([cat, kws], i)=>`
    <div class="keyword-row" data-idx="${i}">
      <input type="text" class="cat-name" value="${cat}" placeholder="分类名">
      <input type="text" class="kw-input" value="${(kws||[]).join(', ')}" placeholder="关键词，逗号分隔">
      <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">删除</button>
    </div>
  `).join('');
}

function addKeywordRow(){
  const el = document.getElementById('keywordRows');
  const div = document.createElement('div');
  div.className = 'keyword-row';
  div.innerHTML = `
    <input type="text" class="cat-name" placeholder="分类名">
    <input type="text" class="kw-input" placeholder="关键词，逗号分隔">
    <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">删除</button>
  `;
  el.appendChild(div);
}

function saveSettings(){
  // Parse file types: empty input = "all" (no filter); otherwise custom ext list
  const ftRaw = document.getElementById('setFileTypes').value.trim();
  const ftList = ftRaw ? ftRaw.split(/[,，;；\s]+/).map(s=>s.trim()).filter(Boolean) : [];
  const data = {
    api_base: document.getElementById('setApiBase').value.trim(),
    api_model: document.getElementById('setApiModel').value.trim(),
    ai_send_paths: document.getElementById('setAiSendPaths').checked,
    min_size_kb: parseInt(document.getElementById('setMinSize').value)||1,
    file_types: ftList.length ? ftList : 'all',
  };
  // 留空 = 不修改已保存的 Key，避免空串覆盖
  const keyVal = document.getElementById('setApiKey').value.trim();
  if(keyVal) data.api_key = keyVal;
  // Collect keywords
  const keywords = {};
  document.querySelectorAll('#keywordRows .keyword-row').forEach(row=>{
    const cat = row.querySelector('.cat-name').value.trim();
    const kws = row.querySelector('.kw-input').value.trim();
    if(cat && kws){
      keywords[cat] = kws.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
    }
  });
  data.organize_keywords = keywords;

  api('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(data)
  }).then(r=>r.json()).then(()=>{
    // Also save keywords separately
    return api('/api/organize/keywords', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(keywords)
    });
  }).then(()=>{
    closeSettings();
    toast('设置已保存', 'success');
  });
}

// ─── Utils ───
function showOpProgress(title, total){
  document.getElementById('opProgressTitle').textContent = title;
  document.getElementById('opProgressFill').style.width = '0%';
  document.getElementById('opProgressText').textContent = `0 / ${total}`;
  document.getElementById('opProgressOverlay').classList.add('show');
}
function updateOpProgress(idx, total, path){
  const pct = Math.round(idx/total*100);
  document.getElementById('opProgressFill').style.width = pct+'%';
  document.getElementById('opProgressText').textContent =
    `${idx} / ${total} · ${path ? path.split('\\').pop() : ''}`;
}
function hideOpProgress(){
  document.getElementById('opProgressOverlay').classList.remove('show');
}

function formatSize(n){
  for(const u of ['B','KB','MB','GB','TB']){
    if(n<1024) return n.toFixed(1)+' '+u;
    n/=1024;
  }
  return (n).toFixed(1)+' PB';
}

function closeModal(id){
  document.getElementById(id).classList.remove('show');
}

function toast(msg, type){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show '+ (type||'');
  setTimeout(()=>t.classList.remove('show'), 3000);
}
