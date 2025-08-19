(async function(){
  // Sidebar toggle
  const sidebar = document.getElementById('sidebar');
  const toggleBtn = document.getElementById('toggle-sidebar');
  const rightBar = document.getElementById('rightbar');
  const toggleRight = document.getElementById('toggle-right');
  if(toggleBtn && sidebar){
    const updateIcon = ()=>{
      const isCollapsed = sidebar.classList.contains('collapsed');
      toggleBtn.textContent = isCollapsed ? '>' : '<';
      toggleBtn.setAttribute('aria-pressed', isCollapsed ? 'true' : 'false');
    };
    toggleBtn.addEventListener('click', ()=>{
      sidebar.classList.toggle('collapsed');
      updateIcon();
    });
    updateIcon();
  }

  // Right sidebar toggle (LangGraph)
  function setRightbar(open){
    if(!rightBar) return;
    if(open) rightBar.classList.remove('collapsed'); else rightBar.classList.add('collapsed');
    if(toggleRight) toggleRight.setAttribute('aria-pressed', open ? 'true' : 'false');
    if(toggleRight) toggleRight.textContent = open ? '<' : '>';
  }
  if(toggleRight){
    toggleRight.addEventListener('click', ()=>{
      const open = !!(rightBar && rightBar.classList.contains('collapsed'));
      setRightbar(open);
    });
  }

  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  const pageSel = document.getElementById('page-select');
  const methodSel = document.getElementById('retrieval-method');
  const out = document.getElementById('chat-output');
  const editContextBtn = document.getElementById('edit-context');
  const contextTextarea = document.getElementById('context-text');
  const saveContextBtn = document.getElementById('save-context');
  let systemContext = null;

  if(form){
    form.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const message = input.value.trim();
      if(!message) return;
      out.textContent += `\n> ${message}\n`;
      out.textContent += `… sending to server, please wait …\n`;
      input.value = '';
      const sendBtn = form.querySelector('button[type="submit"]');
      const originalText = sendBtn ? sendBtn.textContent : '';
      if(sendBtn){
        sendBtn.disabled = true;
        sendBtn.classList.add('processing');
        sendBtn.textContent = 'Working…';
      }
      try{
        const controller = new AbortController();
        const res = await fetch('/api/chat/stream', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ message, page_slug: pageSel.value || null, retrieval_method: (methodSel && methodSel.value) || 'vector', selected_html: (selectedNode||null), selected_path: (selectedPath||[]), system_context: systemContext }),
          signal: controller.signal
        });
        if(!res.ok){
          const text = await res.text();
          out.textContent += `Error: HTTP ${res.status} - ${text}\n`;
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while(true){
          const {value, done} = await reader.read();
          if(done) break;
          buffer += decoder.decode(value, {stream:true});
          let idx;
          while((idx = buffer.indexOf("\n\n")) !== -1){
            const chunk = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const lines = chunk.split(/\n/);
            let event = 'message';
            let data = '';
            for(const line of lines){
              if(line.startsWith('event:')) event = line.slice(6).trim();
              if(line.startsWith('data:')) data += line.slice(5).trim();
            }
            if(event === 'started'){
              out.textContent += `… request received …\n`;
            } else if(event === 'phase'){
              try{ const j = JSON.parse(data); out.textContent += `… ${j.name} …\n`; }catch{}
            } else if(event === 'retrieved'){
              try{ const j = JSON.parse(data); out.textContent += `retrieved ${j.num_chunks} chunks in ${Math.round((j.timings||{}).retrieve_ms||0)}ms\n`; }catch{}
            } else if(event === 'error'){
              try{ const j = JSON.parse(data); out.textContent += `Error: ${j.message}\n`; }catch{ out.textContent += `Error\n`; }
            } else if(event === 'done'){
              try{
                const j = JSON.parse(data);
                if(j.saved) out.textContent += `Saved page and triggered reload.\n`;
                const timing = j.timings ? `\n[retrieval=${j.retrieval_method}] timings: ${JSON.stringify(j.timings)}` : '';
                out.textContent += (j.answer || "(no answer)") + timing + "\n";
              }catch{}
            }
          }
        }
      }catch(err){ out.textContent += `Error: ${err}`; }
      finally{
        if(sendBtn){
          sendBtn.disabled = false;
          sendBtn.classList.remove('processing');
          sendBtn.textContent = originalText || 'Send';
        }
      }
    });
  }

  // Preview iframe with version navigation
  const previewSlug = document.getElementById('preview-slug');
  const prevBtn = document.getElementById('prev-version');
  const nextBtn = document.getElementById('next-version');
  const versionLabel = document.getElementById('version-label');
  const frame = document.getElementById('preview-frame');
  const pickBtn = document.getElementById('pick-element');
  const retrievalSelect = document.getElementById('retrieval-method');
  let pickActive = false;
  let selectedPath = [];
  let selectedNode = null;
  let versions = [];
  let currentVersionIdx = 0;

  async function loadVersions() {
    if(!previewSlug) return;
    const slug = previewSlug.value;
    try{
      const res = await fetch(`/api/versions?slug=${encodeURIComponent(slug)}`);
      const data = await res.json();
      versions = Array.isArray(data.versions) ? data.versions.slice() : [];
      // Ensure 'current' is present at index 0
      if(!versions.length || versions[0] !== 'current'){
        versions = ['current', ...versions.filter(v => v !== 'current')];
      }
      currentVersionIdx = 0; // start at current
      updateFrame();
    }catch(e){ /* ignore */ }
  }

  function updateFrame(){
    const slug = previewSlug.value;
    if(!versions.length){
      frame.src = `/pages/${slug}/index.html`;
      versionLabel.textContent = 'current';
      return;
    }
    const v = versions[currentVersionIdx] ?? 'current';
    if(v === 'current'){
      frame.src = `/pages/${slug}/index.html`;
      fetch(`/api/versions?slug=${encodeURIComponent(slug)}`)
        .then(r=>r.json())
        .then(j=>{ versionLabel.textContent = (j.current || 'v.1') + ' (current)'; })
        .catch(()=>{ versionLabel.textContent = 'v.1 (current)'; });
    }else{
      frame.src = `/pages/${slug}/versions/${v}.html`;
      versionLabel.textContent = v;
    }
  }

  if(previewSlug){
    previewSlug.addEventListener('change', loadVersions);
    loadVersions();
  }
  if(prevBtn){
    prevBtn.addEventListener('click', ()=>{
      if(!versions.length) return;
      // Move to older snapshot (index +1) but cap at last index
      currentVersionIdx = Math.min(versions.length - 1, currentVersionIdx + 1);
      updateFrame();
    });
  }
  if(nextBtn){
    nextBtn.addEventListener('click', ()=>{
      if(!versions.length) return;
      // Move towards current (index -1) but not below 0
      currentVersionIdx = Math.max(0, currentVersionIdx - 1);
      updateFrame();
    });
  }
  // Element picker injection
  function injectPicker(){
    if(!frame || !frame.contentWindow) return;
    const w = frame.contentWindow; const d = w.document;
    const styleId = '__wpg_picker_style__';
    if(!d.getElementById(styleId)){
      const st = d.createElement('style'); st.id = styleId; st.textContent = `.__wpg_hover{outline: 2px dashed #22c55e !important; outline-offset: 2px !important}`; d.head.appendChild(st);
    }
    // Clear any prior highlights from previous pick sessions
    try{ d.querySelectorAll('.__wpg_hover').forEach(el=>el.classList.remove('__wpg_hover')); }catch{}
    let hoverEl = null;
    function onMouseOver(e){ if(hoverEl) hoverEl.classList.remove('__wpg_hover'); hoverEl = e.target; hoverEl.classList.add('__wpg_hover'); }
    function buildPath(el){ const parts=[]; let cur=el; while(cur && cur.nodeType===1 && cur.tagName.toLowerCase()!=='html'){ const tag=cur.tagName.toLowerCase(); const id=cur.id?`#${cur.id}`:''; const cls=(cur.className&&typeof cur.className==='string')?'.'+cur.className.trim().split(/\s+/).join('.'):''; const index=(()=>{ let i=1; let sib=cur; while((sib=sib.previousElementSibling)!=null){ if(sib.tagName===cur.tagName) i++; } return i; })(); parts.unshift(`${tag}${id}${cls}:nth-of-type(${index})`); cur = cur.parentElement; } return parts; }
    function cleanup(){ try{ if(hoverEl) hoverEl.classList.remove('__wpg_hover'); d.querySelectorAll('.__wpg_hover').forEach(el=>el.classList.remove('__wpg_hover')); }catch{} }
    function onClick(e){ e.preventDefault(); e.stopPropagation(); const el=e.target; const path=buildPath(el); const outer=el.outerHTML; cleanup(); w.parent.postMessage({type:'WPG_PICK', outerHTML: outer, path: path}, '*'); w.removeEventListener('mouseover', onMouseOver, true); w.removeEventListener('click', onClick, true); }
    w.addEventListener('mouseover', onMouseOver, true);
    w.addEventListener('click', onClick, true);
  }
  if(pickBtn){
    pickBtn.addEventListener('click', ()=>{ pickActive = true; injectPicker(); });
  }
  if(editContextBtn){
    editContextBtn.addEventListener('click', ()=>{
      if(contextTextarea) contextTextarea.value = systemContext || "You are an expert frontend developer familiar with the latest frontend JS frameworks and tasked as a contractor to create SPAs with enterprise-grade professional designs. Make modern-looking pages with tasteful graphics, subtle animations, and modals where appropriate. Here is your task from the client:";
      // Prefer data-bs-toggle to allow Bootstrap to manage in all browsers; fallback JS open for older cases
      if(!window.bootstrap){
        const modalEl = document.getElementById('contextModal');
        if(modalEl) modalEl.style.display = 'block';
      }
    });
  }
  if(saveContextBtn){
    saveContextBtn.addEventListener('click', ()=>{ systemContext = (contextTextarea && contextTextarea.value) ? contextTextarea.value : null; });
  }
  window.addEventListener('message', (ev)=>{
    if(!ev || !ev.data || ev.data.type !== 'WPG_PICK') return;
    pickActive = false;
    selectedPath = ev.data.path || [];
    selectedNode = ev.data.outerHTML || '';
    // Populate a context viewer below retrieval
    ensureContextPanel();
    renderContextPanel();
  });

  function ensureContextPanel(){
    const sidebar = document.getElementById('sidebar');
    if(!sidebar) return;
    if(document.getElementById('context-panel')) return;
    const panel = document.createElement('div');
    panel.id = 'context-panel';
    panel.className = 'card bg-transparent border-0 mt-2';
    panel.innerHTML = `
      <div class="card-body">
        <h6 class="card-title text-white mb-2">Selection Context</h6>
        <div id="context-path" class="text-white-50 small mb-2"></div>
        <div id="context-html" class="text-white small" style="max-height:160px; overflow:auto; background:#0e1730; border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:8px;"></div>
      </div>`;
    const chatCard = sidebar.querySelector('.card.chat');
    if(chatCard && chatCard.parentElement){ chatCard.parentElement.insertBefore(panel, chatCard.nextSibling); }
    else sidebar.appendChild(panel);
  }
  function renderContextPanel(){
    const pathEl = document.getElementById('context-path');
    const htmlEl = document.getElementById('context-html');
    if(!pathEl || !htmlEl) return;
    // Breadcrumb-like path with collapse (simple text for now)
    pathEl.textContent = (selectedPath || []).join(' > ');
    htmlEl.textContent = selectedNode || '';
  }
  try{
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev)=>{
      if(ev.data === 'reload') location.reload();
    };
  }catch(e){ /* ignore */ }
})();
