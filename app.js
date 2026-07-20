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

// ─── Init ───
document.addEventListener('DOMContentLoaded', () => {
  loadConfig();
  loadFolders();
  loadPresets();
  // Cancel button works for whichever tab is being scanned
  document.getElementById('cancelScanBtn').onclick = cancelCurrentScan;
  // Check previous dedup result (restored from server-side cache after restart)
  fetch('/api/scan/result').then(r=>r.json()).then(d=>{
    if(d.groups && d.groups.length>=0 && d.total_files>0){
      scanResult = d;
      renderAll();
    }
  });
  // Check previous organize result
  fetch('/api/organize/result').then(r=>r.json()).then(d=>{
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
  fetch('/api/config').then(r=>r.json()).then(cfg=>{
    document.getElementById('modeSelect').value = cfg.scan_mode || 'exact';
    localStorage.setItem('scan_mode', cfg.scan_mode || 'exact');
    hasSend2trash = cfg.has_send2trash !== false;
  });
}

function cancelCurrentScan(){
  const url = currentTab==='dedup' ? '/api/scan/cancel' : '/api/organize/cancel';
  fetch(url, {method:'POST'}).then(()=>toast('正在取消...'));
}

function loadFolders(){
  const el = document.getElementById('folderItems');
  if(el) el.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px">加载中...</div>';
  fetch('/api/folders').then(r=>r.json()).then(renderFolders);
}

function loadPresets(){
  fetch('/api/presets').then(r=>r.json()).then(presets=>{
    const el = document.getElementById('presetsRow');
    if(!presets.length){ el.innerHTML=''; return; }
    el.innerHTML = '<span style="font-size:12px;color:var(--text-dim);margin-right:4px">预设:</span>' +
      presets.map(p=>`<span class="chip" onclick="addPreset('${p.path}','${p.label}')">${p.label} +</span>`).join('');
  });
}

function addPreset(path, label){
  fetch('/api/folders', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path, label, protected:false})
  }).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    toast('已添加', 'success');
    loadFolders();
  });
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
  fetch('/api/folders/pick', {method:'POST'})
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
  fetch('/api/folders', {
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
  fetch('/api/folders?path='+encodeURIComponent(path), {method:'DELETE'})
    .then(r=>r.json()).then(()=>{ loadFolders(); });
}

function toggleProtect(path){
  fetch('/api/folders/protect', {
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
  fetch('/api/scan', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    document.getElementById('progressTitle').textContent = '扫描重复文件中...';
    document.getElementById('progressOverlay').classList.add('show');
    pollScan();
  });
}

function pollScan(){
  fetch('/api/scan/status').then(r=>r.json()).then(s=>{
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
  fetch('/api/scan/result').then(r=>r.json()).then(d=>{
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

  const sources = new Set();
  scanResult.groups.forEach(g=>g.files.forEach(f=>sources.add(f.source.replace('🔒 ',''))));
  document.getElementById('keepLabelSelect').innerHTML =
    '<option value="">保留指定来源...</option>' +
    [...sources].map(s=>`<option value="${s}">${s}</option>`).join('');

  renderGroups();
}

function renderGroups(){
  if(!scanResult) return;
  const search = document.getElementById('searchInput').value.toLowerCase();
  const sort = document.getElementById('sortSelect').value;
  let groups = [...scanResult.groups];
  if(currentFilter) groups = groups.filter(g=>g.ext === currentFilter);
  if(search) groups = groups.filter(g=>g.filename.toLowerCase().includes(search));
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
          ${i===0?'<span class="keep-tag">建议保留</span>':''}
          <span class="f-path" title="${f.path}">${f.path}</span>
          <span class="f-date">${f.mtime_str}</span>
          <span class="f-size">${formatSize(f.size)}</span>
          <span class="f-actions">
            <button class="f-act-btn" title="打开文件" data-path="${f.path}" data-dir="0" onclick="openPathBtn(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M19 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h6"/></svg></button>
            <button class="f-act-btn" title="打开所在目录" data-path="${f.path}" data-dir="1" onclick="openPathBtn(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></button>
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
  fetch('/api/config', {
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
  updateActionBar();
}

// ─── Open file / reveal in Explorer ───
function openPathBtn(btn){
  fetch('/api/open', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path: btn.dataset.path, dir: btn.dataset.dir === '1'})
  }).then(r=>r.json()).then(d=>{
    if(d.error) toast(d.error, 'error');
  }).catch(()=>toast('打开失败', 'error'));
}

function clearSelections(){
  if(currentTab==='dedup'){
    selectedFiles.clear();
    document.querySelectorAll('#tabDedup .file-row input[type=checkbox]').forEach(cb=>cb.checked=false);
  } else {
    orgSelectedFiles.clear();
    document.querySelectorAll('#tabOrganize .file-row input[type=checkbox]').forEach(cb=>cb.checked=false);
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
  fetch('/api/quick_rule', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({rule, groups: scanResult.groups, keep_label: label})
  }).then(r=>r.json()).then(d=>{
    selectedFiles.clear();
    d.selections.forEach(id=>selectedFiles.add(id));
    renderGroups();
    toast(`已勾选 ${d.selections.length} 个文件`, 'success');
  });
}

// ─── DEDUP: AI ───
function applyAI(){
  const text = document.getElementById('aiInput').value.trim();
  if(!text) return;
  if(!scanResult) return toast('请先扫描', 'error');
  document.getElementById('aiBtn').disabled = true;
  document.getElementById('aiBtn').textContent = '分析中...';
  fetch('/api/ai', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text, groups: scanResult.groups})
  }).then(r=>r.json()).then(d=>{
    document.getElementById('aiBtn').disabled = false;
    document.getElementById('aiBtn').textContent = '执行';
    if(d.error) return toast(d.error, 'error');
    const ue = document.getElementById('aiUnderstanding');
    ue.style.display = 'block';
    ue.textContent = 'AI 理解：' + (d.understanding || '(无说明)');
    if(d.selections && Array.isArray(d.selections)){
      selectedFiles.clear();
      d.selections.forEach(id=>selectedFiles.add(id));
      renderGroups();
      toast(`AI 勾选了 ${d.selections.length} 个文件，请确认后操作`, 'success');
    }
  }).catch(e=>{
    document.getElementById('aiBtn').disabled = false;
    document.getElementById('aiBtn').textContent = '执行';
    toast('AI请求失败: '+e.message, 'error');
  });
}

// ══════════════════════════════════════════
// ─── ORGANIZE: Scan ───
// ══════════════════════════════════════════
function startOrganizeScan(){
  fetch('/api/organize/scan', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.error) return toast(d.error, 'error');
    document.getElementById('progressTitle').textContent = '整理扫描中...';
    document.getElementById('progressOverlay').classList.add('show');
    pollOrganizeScan();
  });
}

function pollOrganizeScan(){
  fetch('/api/organize/status').then(r=>r.json()).then(s=>{
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
  fetch('/api/organize/result').then(r=>r.json()).then(d=>{
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

function renderCategories(){
  if(!organizeResult) return;
  const search = document.getElementById('orgSearchInput').value.toLowerCase();
  let categories = [...organizeResult.categories];

  // Filter by ext
  if(orgCurrentFilter){
    categories = categories.map(c=>({...c, files:c.files.filter(f=>f.ext===orgCurrentFilter)}))
                           .filter(c=>c.files.length > 0);
  }
  // Filter by search
  if(search){
    categories = categories.map(c=>({...c, files:c.files.filter(f=>f.name.toLowerCase().includes(search))}))
                           .filter(c=>c.files.length > 0);
  }

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
  // Get visible categories (same filtering as renderCategories)
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
  const cat = categories[ci];
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
  organizeResult.categories.forEach(c=>c.files.forEach(f=>{
    if(!f.protected) orgSelectedFiles.add(f.id);
  }));
  renderCategories();
  updateActionBar();
}

function orgSelectNone(){
  orgSelectedFiles.clear();
  renderCategories();
  updateActionBar();
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
    fetch('/api/operate', {
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
  fetch('/api/folders/pick', {method:'POST'})
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
  fetch('/api/folders/pick', {method:'POST'})
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
    // files[0] is the oldest copy = the one marked 建议保留 (matches backend)
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
function exportData(fmt){
  window.open('/api/export?format='+fmt);
}

// ─── Log ───
function showLog(){
  fetch('/api/log').then(r=>r.text()).then(t=>{
    document.getElementById('logContent').textContent = t || '(空)';
    document.getElementById('logModal').classList.add('show');
  });
}

// ─── Settings ───
let fileTypeDefaults = [];   // built-in default ext list from backend

function fillDefaultFileTypes(){
  document.getElementById('setFileTypes').value = fileTypeDefaults.join(', ');
}

function showSettings(){
  fetch('/api/config').then(r=>r.json()).then(cfg=>{
    document.getElementById('setApiKey').value = cfg.api_key === '***' ? '' : (cfg.api_key||'');
    document.getElementById('setApiBase').value = cfg.api_base || 'https://api.openai.com/v1';
    document.getElementById('setApiModel').value = cfg.api_model || 'gpt-4o-mini';
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
    api_key: document.getElementById('setApiKey').value.trim(),
    api_base: document.getElementById('setApiBase').value.trim(),
    api_model: document.getElementById('setApiModel').value.trim(),
    min_size_kb: parseInt(document.getElementById('setMinSize').value)||1,
    file_types: ftList.length ? ftList : 'all',
  };
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

  fetch('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(data)
  }).then(r=>r.json()).then(()=>{
    // Also save keywords separately
    return fetch('/api/organize/keywords', {
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
