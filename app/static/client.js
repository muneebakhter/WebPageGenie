(async function(){
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  const pageSel = document.getElementById('page-select');
  const out = document.getElementById('chat-output');

  if(form){
    form.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const message = input.value.trim();
      if(!message) return;
      out.textContent += `\n> ${message}\n`;
      input.value = '';
      try{
        const res = await fetch('/api/chat', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ message, page_slug: pageSel.value || null })
        });
        const data = await res.json();
        if(data.saved){ out.textContent += "Saved page and triggered reload.\n"; }
        out.textContent += (data.answer || "(no answer)") + "\n";
      }catch(err){ out.textContent += `Error: ${err}`; }
    });
  }

  try{
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev)=>{
      if(ev.data === 'reload') location.reload();
    };
  }catch(e){ /* ignore */ }
})();
