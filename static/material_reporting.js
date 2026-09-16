/* Material search belongs only to the history page. */
(async()=>{
 const mount=document.getElementById('materialReportMount');if(!mount)return;
 const query=new URLSearchParams(location.search),line=query.get('line')||query.get('line_id');
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=value=>value===null?'—':Number(value||0).toLocaleString('tr-TR');
 const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Istanbul'}).format(new Date());
 const ago=new Date(`${today}T12:00:00`);ago.setDate(ago.getDate()-15);
 const defaultStart=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Istanbul'}).format(ago);
 mount.innerHTML=`<div class="card"><div class="card-label">Malzemeye Göre Üretim ve Kalite</div><div class="material-filter"><label>Malzeme<select id="historyMaterial"><option value="">Tüm malzemeler</option><option value="0">Belirsiz</option></select></label><label>Başlangıç<input id="historyStart" type="date"></label><label>Bitiş<input id="historyEnd" type="date"></label><button id="historyShow">Göster</button><a id="historyCsv">Excel için CSV</a></div><div class="material-summary" id="historyTotals">Yükleniyor…</div><div class="material-note" id="historyNote"></div><div class="material-table"><table><thead><tr><th>Tarih</th><th>Vardiya</th><th>Malzeme</th><th>Üretim</th><th>Fire</th><th>Sağlam</th></tr></thead><tbody id="historyRows"></tbody></table></div></div>`;
 const el=id=>document.getElementById(id),get=async url=>{const response=await fetch(url);let data=null;try{data=await response.json()}catch{}if(!response.ok)throw new Error(data?.detail||`HTTP ${response.status}`);return data};
 el('historyStart').value=query.get('date_from')||defaultStart;el('historyEnd').value=query.get('date_to')||today;
 window.materialHistoryFilter=()=>({start:el('historyStart').value,end:el('historyEnd').value,material_id:el('historyMaterial').value});
 try{
  const materials=await get('/api/v1/assignments/materials?include_inactive=true');
  el('historyMaterial').insertAdjacentHTML('beforeend',materials.map(m=>`<option value="${m.material_id}">${esc(m.material_code)} · ${esc(m.material_name)}</option>`).join(''));
  el('historyMaterial').value=query.get('material_id')||'';
  async function refresh(){
   const start=el('historyStart').value,end=el('historyEnd').value,material=el('historyMaterial').value;
   if(!start||!end){el('historyTotals').textContent='Başlangıç ve bitiş tarihini seçin.';return}
   const params=new URLSearchParams({line_id:line,start,end});if(material!=='')params.set('material_id',material);
   const url=new URL(location.href);url.searchParams.set('line',line);url.searchParams.delete('line_id');url.searchParams.set('date_from',start);url.searchParams.set('date_to',end);if(material!=='')url.searchParams.set('material_id',material);else url.searchParams.delete('material_id');history.replaceState(null,'',url);
   el('historyTotals').textContent='Yükleniyor…';
   try{
    const data=await get('/api/v1/material-report?'+params);
    el('historyTotals').textContent=`Üretim ${num(data.total_produced)} · Fire ${num(data.total_scrap)} · Sağlam ${num(data.total_good)}`;
    el('historyNote').textContent=data.basis+' OEE grafiği hat toplamıdır.';
    el('historyRows').innerHTML=data.items.map(r=>`<tr><td>${esc(r.date)}</td><td>${esc(r.shift)}</td><td><b>${esc(r.material_code)}</b><br><small>${esc(r.material_name)}</small></td><td>${num(r.produced)}</td><td>${num(r.scrap)}</td><td>${num(r.good)}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">Seçilen aralıkta kayıt yok.</td></tr>';
    el('historyCsv').href='/api/v1/material-report/csv?'+params;
    if(typeof loadProduction==='function')await loadProduction();
   }catch(error){el('historyTotals').textContent=error.message;el('historyRows').innerHTML='';}
  }
  el('historyShow').onclick=refresh;el('historyMaterial').onchange=refresh;
  await refresh();
 }catch(error){el('historyTotals').textContent='Malzeme listesi alınamadı: '+error.message;}
})();
