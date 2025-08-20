(async function(){
  // Sidebar toggle
  const sidebar = document.getElementById('sidebar');
  const toggleBtn = document.getElementById('toggle-sidebar');
  const rightBar = document.getElementById('rightbar');
  const toggleRight = document.getElementById('toggle-right');
  if(toggleBtn && sidebar){
    const updateIcon = ()=>{
      const isCollapsed = sidebar.classList.contains('collapsed');
      const icon = toggleBtn.querySelector('i');
      if(icon){ icon.className = isCollapsed ? 'bi bi-chevron-right' : 'bi bi-chevron-left'; }
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
    if(toggleRight){
      const icon = toggleRight.querySelector('i');
      if(icon){ icon.className = open ? 'bi bi-chevron-left' : 'bi bi-chevron-right'; }
    }
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
  const pageDD = document.getElementById('page-suggestions');
  const methodSel = document.getElementById('retrieval-method');
  const out = document.getElementById('chat-output');
  const refSection = document.getElementById('ref-section');
  const refUrl = document.getElementById('ref-url');
  const refCheck = document.getElementById('ref-check');
  const refStatus = document.getElementById('ref-status');
  const refImages = document.getElementById('ref-images');
  let outputModal = null;
  let bootstrapModal = null;
  const editContextBtn = document.getElementById('edit-context');
  const contextTextarea = document.getElementById('context-text');
  const saveContextBtn = document.getElementById('save-context');
  let systemContext = null;

  const pageList = (()=>{ try{ const el=document.getElementById('pages-data'); return el ? JSON.parse(el.textContent||'[]') : []; }catch(e){ return []; } })();

  function showSuggestions(query){
    if(!pageDD) return;
    const q = (query||'').toLowerCase();
    const matches = pageList.filter(p => p.toLowerCase().includes(q)).slice(0, 8);
    const items = matches.map(p => `<button type="button" class="dropdown-item" data-value="${p}">${p}</button>`).join('');
    pageDD.innerHTML = items || `<div class="dropdown-item disabled">No matches — will create “${query || ''}”</div>`;
    pageDD.style.display = 'block';
  }
  function hideSuggestions(){ if(pageDD) pageDD.style.display='none'; }

  if(pageSel){
    function updateRefSection(){
      const name = (pageSel.value || '').trim();
      const isExisting = !!pageList.find(p => p.toLowerCase() === name.toLowerCase());
      const isNew = !!name && !isExisting;
      if(refSection){ refSection.style.display = isNew ? '' : 'none'; }
      if(refUrl){ refUrl.disabled = !isNew; if(isNew && !refUrl.value) refUrl.value = ''; }
      if(refCheck){ refCheck.disabled = !isNew; }
      if(refImages){ refImages.disabled = !isNew; refImages.checked = false; }
      if(refStatus){ refStatus.style.display = 'none'; refStatus.textContent = ''; refStatus.className = 'input-group-text'; }
    }
    pageSel.addEventListener('input', (e)=>{ showSuggestions(pageSel.value); updateRefSection(); });
    pageSel.addEventListener('focus', ()=>{ showSuggestions(pageSel.value); });
    pageSel.addEventListener('blur', ()=>{ setTimeout(hideSuggestions, 150); });
    // Initialize once
    updateRefSection();
  }
  if(pageDD){
    pageDD.addEventListener('click', (e)=>{
      const t = e.target; if(!(t instanceof Element)) return;
      const val = t.getAttribute('data-value');
      if(val && pageSel){ pageSel.value = val; hideSuggestions(); if(typeof updateRefSection==='function') updateRefSection(); }
    });
  }

  if(refCheck && refUrl){
    refCheck.addEventListener('click', async ()=>{
      const url = (refUrl.value || '').trim();
      if(!url) return;
      // Reset status UI
      if(refStatus){ refStatus.style.display=''; refStatus.textContent='…'; refStatus.className='input-group-text'; }
      try{
        const res = await fetch('/api/tools/validate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ url })});
        const j = await res.json();
        const ok = !!(j && j.ok);
        if(refStatus){
          refStatus.style.display='';
          refStatus.textContent = ok ? '✓' : '✕';
          refStatus.className = 'input-group-text ' + (ok ? 'text-success' : 'text-danger');
        }
        // If accessible, fetch example site assets and write to output modal
        if(ok){
          const res2 = await fetch('/api/tools/example/scrape', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ url, save_images: !!(refImages && refImages.checked), page_slug: (pageSel && pageSel.value) || null })});
          const j2 = await res2.json();
          const out = document.getElementById('chat-output');
          const modalEl = document.getElementById('outputModal');
          if(out){
            out.textContent = [
              'Example site analysis (for AI context):',
              j2.summary || '(no summary)',
              '',
              'Detected frameworks: ' + ((j2.frameworks||[]).join(', ') || 'none'),
              '',
              (Array.isArray(j2.images) && j2.images.length ? ('Saved images (' + j2.images.length + '):\n' + j2.images.map(i => `- ${i.path}  alt="${i.alt||''}"`).join('\n')) : 'No images saved'),
              '',
              'Combined CSS (truncated):',
              (j2.css_combined || '').slice(0, 3000),
              '',
              'Combined JS (truncated):',
              (j2.js_combined || '').slice(0, 3000),
            ].join('\n');
          }
          try{ if(window.bootstrap && modalEl){ const m=new window.bootstrap.Modal(modalEl); m.show(); } }catch{}
        }
      }catch(err){
        if(refStatus){ refStatus.style.display=''; refStatus.textContent='✕'; refStatus.className='input-group-text text-danger'; }
      }
    });
  }

  if(form){
    form.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const message = input.value.trim();
      if(!message) return;
      if(out){ out.textContent = `> ${message}\n… sending to server, please wait …\n`; }
      try{
        const el = document.getElementById('outputModal');
        if(el && window.bootstrap){ bootstrapModal = new window.bootstrap.Modal(el); bootstrapModal.show(); }
        else if(el){ el.style.display = 'block'; }
      }catch{}
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
          body: JSON.stringify({ message, page_slug: (pageSel && pageSel.value ? pageSel.value.trim() : null), retrieval_method: (methodSel && methodSel.value) || 'vector', selected_html: (selectedNode||null), selected_path: (selectedPath||[]), system_context: systemContext }),
          signal: controller.signal
        });
        if(!res.ok){
          const text = await res.text();
          if(out){ out.textContent += `Error: HTTP ${res.status} - ${text}\n`; }
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
              if(out){ out.textContent += `… request received …\n`; }
            } else if(event === 'phase'){
              try{ const j = JSON.parse(data); if(out){ out.textContent += `… ${j.name} …\n`; } }catch{}
            } else if(event === 'retrieved'){
              try{ const j = JSON.parse(data); if(out){ out.textContent += `retrieved ${j.num_chunks} chunks in ${Math.round((j.timings||{}).retrieve_ms||0)}ms\n`; } }catch{}
            } else if(event === 'error'){
              try{ const j = JSON.parse(data); if(out){ out.textContent += `Error: ${j.message}\n`; } }catch{ if(out){ out.textContent += `Error\n`; } }
            } else if(event === 'done'){
              try{
                const j = JSON.parse(data);
                if(out && j.saved) out.textContent += `Saved page and triggered reload.\n`;
                const timing = j.timings ? `\n[retrieval=${j.retrieval_method}] timings: ${JSON.stringify(j.timings)}` : '';
                if(out){ out.textContent += (j.answer || "(no answer)") + timing + "\n"; }
              }catch{}
            }
          }
        }
      }catch(err){ if(out){ out.textContent += `Error: ${err}`; } }
      finally{
        if(sendBtn){
          sendBtn.disabled = false;
          sendBtn.classList.remove('processing');
          sendBtn.textContent = originalText || 'Send';
        }
        try{ if(bootstrapModal){ bootstrapModal.handleUpdate(); } }catch{}
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
  const vpMobile = document.getElementById('vp-mobile');
  const vpTablet = document.getElementById('vp-tablet');
  const vpDesktop = document.getElementById('vp-desktop');
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
  // (Reverted enhanced select/toast)
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
  // Viewport toggles
  function setViewport(width){
    if(!frame) return;
    frame.style.maxWidth = width ? width + 'px' : '';
    frame.style.margin = width ? '0 auto' : '';
  }
  if(vpMobile){ vpMobile.addEventListener('click', ()=> setViewport(390)); }
  if(vpTablet){ vpTablet.addEventListener('click', ()=> setViewport(768)); }
  if(vpDesktop){ vpDesktop.addEventListener('click', ()=> setViewport(null)); }
  // Element picker injection
  function injectPicker(){
    if(!frame || !frame.contentWindow) return;
    const w = frame.contentWindow; const d = w.document;
    const styleId = '__wpg_picker_style__';
    if(!d.getElementById(styleId)){
      const st = d.createElement('style'); st.id = styleId; st.textContent = `/* reserved */`; d.head.appendChild(st);
    }
    // Clear any prior highlights from previous pick sessions
    try{ d.querySelectorAll('.__wpg_hover').forEach(el=>el.classList.remove('__wpg_hover')); }catch{}
    let hoverEl = null;
    function getEffectiveBg(el){
      let cur = el;
      while(cur && cur !== d.documentElement){
        const cs = w.getComputedStyle(cur);
        const bg = cs.backgroundColor;
        if(bg && !/rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)/.test(bg) && bg !== 'transparent') return bg;
        cur = cur.parentElement;
      }
      return 'rgb(255,255,255)';
    }
    function parseRGB(str){ const m = str.match(/rgba?\(([^)]+)\)/); if(!m) return [255,255,255]; const parts = m[1].split(',').map(s=>parseFloat(s.trim())); return parts.slice(0,3); }
    function luminance([r,g,b]){ r/=255; g/=255; b/=255; const a=[r,g,b].map(v=> v<=0.03928? v/12.92 : Math.pow(((v+0.055)/1.055),2.4)); return 0.2126*a[0]+0.7152*a[1]+0.0722*a[2]; }
    function contrastColor(el){ const rgb = parseRGB(getEffectiveBg(el)); const L = luminance(rgb); return L > 0.6 ? '#000000' : '#ffffff'; }
    function applyHighlight(el){
      const base = contrastColor(el);
      const dashed = base === '#000000' ? 'rgba(0,0,0,0.5)' : 'rgba(255,255,255,0.6)';
      if(!el.dataset.__wpg_prev_outline){ el.dataset.__wpg_prev_outline = el.style.outline || ''; }
      if(!el.dataset.__wpg_prev_outline_offset){ el.dataset.__wpg_prev_outline_offset = el.style.outlineOffset || ''; }
      if(!el.dataset.__wpg_prev_boxshadow){ el.dataset.__wpg_prev_boxshadow = el.style.boxShadow || ''; }
      el.style.outline = `2px dashed ${dashed}`;
      el.style.outlineOffset = '2px';
      el.style.boxShadow = '';
      el.classList.add('__wpg_hover');
    }
    function removeHighlight(el){
      if(!el) return;
      el.style.outline = el.dataset.__wpg_prev_outline || '';
      el.style.outlineOffset = el.dataset.__wpg_prev_outline_offset || '';
      el.style.boxShadow = el.dataset.__wpg_prev_boxshadow || '';
      delete el.dataset.__wpg_prev_outline;
      delete el.dataset.__wpg_prev_outline_offset;
      delete el.dataset.__wpg_prev_boxshadow;
      el.classList.remove('__wpg_hover');
    }
    function onMouseOver(e){ if(hoverEl) removeHighlight(hoverEl); hoverEl = e.target; applyHighlight(hoverEl); }
    function buildPath(el){ const parts=[]; let cur=el; while(cur && cur.nodeType===1 && cur.tagName.toLowerCase()!=='html'){ const tag=cur.tagName.toLowerCase(); const id=cur.id?`#${cur.id}`:''; const cls=(cur.className&&typeof cur.className==='string')?'.'+cur.className.trim().split(/\s+/).join('.'):''; const index=(()=>{ let i=1; let sib=cur; while((sib=sib.previousElementSibling)!=null){ if(sib.tagName===cur.tagName) i++; } return i; })(); parts.unshift(`${tag}${id}${cls}:nth-of-type(${index})`); cur = cur.parentElement; } return parts; }
    function cleanup(){ try{ if(hoverEl) removeHighlight(hoverEl); d.querySelectorAll('.__wpg_hover').forEach(el=>removeHighlight(el)); }catch{} }
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
  const toggleThemeBtn = document.getElementById('toggle-theme');
  // Theme toggle
  function updateThemeIcon(){
    if(!toggleThemeBtn) return;
    const icon = toggleThemeBtn.querySelector('i');
    const isLight = document.body.classList.contains('light');
    if(icon){ icon.className = isLight ? 'bi bi-sun' : 'bi bi-moon'; }
  }
  if(toggleThemeBtn){
    toggleThemeBtn.addEventListener('click', ()=>{
      document.body.classList.toggle('light');
      updateThemeIcon();
      try{ localStorage.setItem('wpg_theme', document.body.classList.contains('light') ? 'light' : 'dark'); }catch(e){}
    });
    try{ const pref = localStorage.getItem('wpg_theme'); if(pref === 'light'){ document.body.classList.add('light'); } }catch(e){}
    updateThemeIcon();
  }
  try{
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev)=>{
      if(ev.data === 'reload') location.reload();
    };
  }catch(e){ /* ignore */ }
})();
