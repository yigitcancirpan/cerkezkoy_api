/* A single material scope for navigation, production totals and quality views. */
(async()=>{
 const q=new URLSearchParams(location.search), planning=location.pathname.endsWith('/planning.html');
 const pageLine=q.get('line')||q.get('line_id'), selected=q.get('material_id')||'';
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=x=>x===null?'—':Number(x||0).toLocaleString('tr-TR');
 const day=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Istanbul'}).format(new Date());
 const box=document.createElement('section');box.id='materialReport';box.style.cssText='margin:16px;padding:16px;border:1px solid #334155;border-radius:12px;background:#111827;color:#e5e7eb;';
 box.innerHTML='<h3 style="margin:0 0 12px">Malzemeye göre üretim ve kalite</h3><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end"><label>Hat<br><select id="mrLine"></select></label><label>Malzeme<br><select id="mrMaterial" style="max-width:100%"></select></label><label>Başlangıç<br><input id="mrStart" type="date"></label><label>Bitiş<br><input id="mrEnd" type="date"></label><button id="mrRefresh">Göster</button><a id="mrCsv">Excel için CSV</a></div><p id="mrTotals"></p><p id="mrHint" style="font-size:12px"></p><div style="overflow:auto"><table style="width:100%;white-space:nowrap"><thead><tr><th>Tarih</th><th>Vardiya</th><th>Malzeme</th><th>Üretim</th><th>Fire</th><th>Sağlam</th></tr></thead><tbody id="mrRows"></tbody></table></div><p style="font-size:12px">Bu tablonun malzeme filtresi üretim/kalite adetlerini ayırır. Alt bölümlerdeki OEE, duruş, enerji ve hat hedefleri hat toplamıdır.</p>';
 document.body.prepend(box);
 const el=id=>document.getElementById(id);
 const get=async url=>{const r=await fetch(url);if(!r.ok)throw new Error((await r.json()).detail||`HTTP ${r.status}`);return r.json()};
 try{
  const [materials,lines]=await Promise.all([get('/api/v1/assignments/materials?include_inactive=true'),get('/api/v1/lines')]);
  el('mrLine').innerHTML=lines.map(x=>`<option value="${x.line_id}">${esc(x.line_name)}</option>`).join('');
  el('mrLine').value=pageLine||String(lines.find(x=>x.line_id===2)?.line_id||lines[0]?.line_id);
  el('mrLine').disabled=!!pageLine;
  const options=planning?materials.filter(x=>x.is_active&&['2962070700','2962070100'].includes(x.material_code)):materials;
  el('mrMaterial').innerHTML=(planning?'':'<option value="">Tüm malzemeler</option><option value="0">Belirsiz</option>')+options.map(x=>`<option value="${x.material_id}">${esc(x.material_code+' · '+x.material_name)}</option>`).join('');
  el('mrMaterial').style.cssText='max-width:min(480px,75vw)';
  el('mrMaterial').value=planning?String(options.find(x=>x.material_code===(q.get('product_code')||'2962070700'))?.material_id||''):selected;
  el('mrStart').value=q.get('date_from')||day;el('mrEnd').value=q.get('date_to')||day;
  const entry=el('retroMaterial');
  if(entry){entry.innerHTML='<option value="">Tek malzemeyse otomatik eşle</option>'+materials.map(x=>`<option value="${x.material_id}">${esc(x.material_code+' · '+x.material_name)}</option>`).join('');if(Number(selected)>0)entry.value=selected;}
  function links(){for(const a of document.querySelectorAll('a[href]')){const u=new URL(a.href,location.origin);if(u.origin!==location.origin||!u.pathname.startsWith('/static/')||u.pathname.endsWith('/home.html'))continue;u.searchParams.set('line',el('mrLine').value);if(el('mrMaterial').value)u.searchParams.set('material_id',el('mrMaterial').value);else u.searchParams.delete('material_id');const m=materials.find(x=>String(x.material_id)===el('mrMaterial').value);if(m)u.searchParams.set('product_code',m.material_code);a.href=u.href;}}
  links();
  el('mrMaterial').onchange=()=>{const u=new URL(location.href);const v=el('mrMaterial').value;if(v)u.searchParams.set('material_id',v);else u.searchParams.delete('material_id');const m=materials.find(x=>String(x.material_id)===v);if(m)u.searchParams.set('product_code',m.material_code);else u.searchParams.delete('product_code');u.searchParams.set('date_from',el('mrStart').value);u.searchParams.set('date_to',el('mrEnd').value);location.href=u.href;};
  let busy=false;
  async function refresh(){if(busy)return;busy=true;try{
   const p=new URLSearchParams({line_id:el('mrLine').value,start:el('mrStart').value,end:el('mrEnd').value});if(el('mrMaterial').value)p.set('material_id',el('mrMaterial').value);
   const data=await get('/api/v1/material-report?'+p);
   el('mrTotals').textContent=`Üretim: ${num(data.total_produced)} · Fire: ${num(data.total_scrap)} · Sağlam: ${num(data.total_good)}`;
   el('mrHint').textContent=data.basis;
   el('mrRows').innerHTML=data.items.map(r=>`<tr><td>${esc(r.date)}</td><td>${esc(r.shift)}</td><td>${esc(r.material_code)}<br><small>${esc(r.material_name)}</small></td><td>${num(r.produced)}</td><td>${num(r.scrap)}</td><td>${num(r.good)}</td></tr>`).join('')||'<tr><td colspan="6">Seçilen aralıkta kayıt yok.</td></tr>';
   el('mrCsv').href='/api/v1/material-report/csv?'+p;
  }catch(e){el('mrTotals').textContent=e.message;}finally{busy=false;}}
  el('mrRefresh').onclick=refresh;el('mrLine').onchange=()=>{links();refresh()};await refresh();
  setInterval(()=>{if(!document.hidden)refresh()},60000);
 }catch(e){el('mrTotals').textContent=e.message;}
})();
