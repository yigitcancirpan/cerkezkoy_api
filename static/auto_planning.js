/* Daily scheduling UI; all computation and persistence are local API operations. */
let AUTO_RUN=null, AUTO_DIRTY=false, AUTO_BUSY=false;
const autoEl=id=>document.getElementById(id);
const autoPreset=[['day8','08.00–16.00',8],['day10','08.00–18.00',10],['day12','08.00–20.00',12],['double','08.00–16.00 + 16.00–24.00',16]];
function autoMessage(message,error=false){autoEl('autoMessage').textContent=message;autoEl('autoMessage').className=error?'hint neg':'hint'}
function autoInvalidate(){AUTO_DIRTY=true;autoEl('autoApprove').disabled=true;if(AUTO_RUN)autoEl('autoState').textContent='Değişiklik var · Yeniden hesaplayın'}
function autoBusy(value){AUTO_BUSY=value;autoEl('autoControls').disabled=value;autoEl('autoGenerate').disabled=value;autoEl('autoRestore').disabled=value;autoEl('autoApprove').disabled=value||!AUTO_RUN||AUTO_DIRTY||AUTO_RUN.status==='approved';autoEl('autoResult').style.pointerEvents=value?'none':''}
function autoNumber(id){const el=autoEl(id);if(el.value===''||!el.checkValidity())throw new Error('Kapasiteleri geçerli, sıfır veya pozitif tam adet olarak girin.');return Number(el.value)}
function autoRequest(){
 const start=autoEl('autoStart').value,days=Number(autoEl('autoDays').value);
 if(!start)throw new Error('Plan başlangıcını seçin.');
 const options=autoPreset.filter(([code])=>autoEl('allow-'+code).checked).map(([code])=>{
  const breakAllowed=autoEl('break-allow-'+code).checked;
  return {code,capacity:autoNumber('cap-'+code),
   break_extra_capacity:breakAllowed?autoNumber('break-cap-'+code):0,
   break_minutes:breakAllowed?autoNumber('break-minutes-'+code):0};
 });
 if(!options.some(x=>x.code==='day8'&&x.capacity>0))throw new Error('08.00–16.00 için sıfırdan büyük günlük sağlam adet kapasitesini girin.');
 const locks=[];
 if(AUTO_RUN&&AUTO_RUN.request.start_date===start&&AUTO_RUN.request.days===days){
   for(const row of AUTO_RUN.result.daily){const d=row.date;if(autoEl('lock-'+d).checked){const qty=autoNumber('qty-'+d),key=autoEl('shift-'+d).value,opt=AUTO_RUN.result.options.find(x=>x.option_key===key);locks.push(key==='off'?{date:d,code:'off',break_work:false,qty}:{date:d,code:opt.code,break_work:opt.break_work,qty})}}
 }
 return {start_date:start,days,options,locks,minimum_closing_stock:autoNumber('autoMinimumStock'),
  workdays:[...document.querySelectorAll('#autoWorkdays input:checked')].map(x=>Number(x.value)),
  include_planned:autoEl('autoPlanned').checked,dispatch_timing:autoEl('autoDispatch').value,
  planning_mode:autoEl('autoMode').value,
  capacity_note:autoEl('autoBasis').textContent.slice(0,300)};
}
async function autoGenerate(){
 if(AUTO_BUSY)return;
 try{const body=autoRequest();autoBusy(true);autoMessage('Günlük üretim ve stoklar hesaplanıyor…');
 AUTO_RUN=await api(`/api/v1/planning/auto/generate?${scopeQuery()}`,{method:'POST',body:JSON.stringify(body)});
 AUTO_DIRTY=false;autoRender();autoMessage('Taslak kaydedildi. Günleri değiştirip sabitleyebilir veya planı onaylayabilirsiniz.');
 await autoApproved();
 }catch(e){autoMessage(typeof e.message==='string'?e.message:'Plan oluşturulamadı.',true)}finally{autoBusy(false)}
}
function autoRowChanged(d,shiftChanged){
 const key=autoEl('shift-'+d).value;
 if(shiftChanged){const opt=AUTO_RUN.result.options.find(x=>x.option_key===key);autoEl('qty-'+d).value=key==='off'?0:opt.capacity;}
 autoEl('lock-'+d).checked=true;autoInvalidate();
}
function autoRender(){
 const run=AUTO_RUN,r=run.result,s=r.summary;
 autoEl('autoResult').hidden=false;autoEl('autoState').textContent=run.status==='approved'?'Onaylanmış plan kaydı':'Taslak · Henüz uygulanmadı';
 autoEl('autoKpis').innerHTML=[['Planlanan üretim',nfmt(s.total_planned)],['Üretilebilir',nfmt(s.total_feasible)],['Hat çalışma saati',nfmt(s.total_hours)],['Dönem sonu stok',nfmt(s.closing_stock)]].map(([label,value])=>`<div class="kpi"><div class="value">${value}</div><div class="label">${label}</div></div>`).join('');
 autoEl('autoWarnings').replaceChildren();
 for(const message of r.warnings){const p=document.createElement('p');p.className='hint warn';p.textContent='• '+message;autoEl('autoWarnings').appendChild(p)}
 autoEl('autoAssumptions').textContent=r.assumptions.join(' ')+' '+r.method;
 const c=r.comparison,flex=c.daily_flexible,balanced=c.balanced_weekly;
 const scenarios=[['daily_flexible',flex],['balanced_weekly',balanced]];
 autoEl('autoComparison').innerHTML='<thead><tr><th>Senaryo</th><th>Toplam saat</th><th>Mola çalışılan gün</th><th>Mola dk</th><th>12+ saatli gün</th><th>Düzen değişimi</th><th>Planlanan</th><th>Min. stok</th><th>Dönem sonu stok</th></tr></thead><tbody>'+scenarios.map(([key,x])=>`<tr class="${c.selected===key?'selected-plan':''}"><td>${esc(x.label)}${c.selected===key?' · Seçilen':''}</td><td>${nfmt(x.total_hours)}</td><td>${nfmt(x.break_work_day_count)}</td><td>${nfmt(x.break_work_minutes)}</td><td>${nfmt(x.long_day_count)}</td><td>${nfmt(x.shift_change_count)}</td><td>${nfmt(x.total_planned)}</td><td>${nfmt(x.minimum_stock)}</td><td>${nfmt(x.closing_stock)}</td></tr>`).join('')+'</tbody>';
 autoEl('autoCostWarning').textContent=c.cost_warning;
 const working=d=>run.request.workdays.includes((new Date(d+'T12:00:00').getDay()+6)%7);
 const optionsFor=d=>working(d)?r.options:[{option_key:'off',label:'Takvimde kapalı'}];
 autoEl('autoTable').innerHTML='<thead><tr><th>Gün</th><th>Çalışma düzeni</th><th>Üretim hedefi</th><th>Mola katkısı</th><th>Üretilebilir</th><th>Sevkiyat</th><th>Gün sonu stok</th><th>Minimum stok</th><th>Minimum üstü marj</th><th>Sevkiyat açığı</th><th>Hammadde sonu</th><th>Sabitle</th><th>Durum</th></tr></thead><tbody>'+r.daily.map(d=>`<tr>
 <td>${trDate(d.date)} ${dayNames[(new Date(d.date+'T12:00:00').getDay()+6)%7]}</td>
 <td><select aria-label="${d.date} çalışma düzeni" id="shift-${d.date}" onchange="autoRowChanged('${d.date}',true)">${optionsFor(d.date).map(o=>`<option value="${o.option_key}" ${o.option_key===d.option_key?'selected':''}>${esc(o.label)}</option>`).join('')}</select></td>
 <td><input aria-label="${d.date} üretim hedefi" id="qty-${d.date}" type="number" min="${working(d.date)?r.minimum_daily_qty:0}" max="${working(d.date)?10000000:0}" step="1" value="${d.planned_qty}" onchange="autoRowChanged('${d.date}',false)"></td>
 <td class="${d.break_work?'warn':'muted'}">${d.break_work?'+'+nfmt(d.break_extra_qty)+' / '+nfmt(d.break_minutes)+' dk':'—'}</td><td>${nfmt(d.feasible_qty)}</td><td>${nfmt(d.order_qty)}</td><td class="${cls(d.closing_finished_stock)}">${nfmt(d.closing_finished_stock)}</td>
 <td>${nfmt(d.minimum_closing_stock)}</td><td class="${cls(d.stock_margin)}">${nfmt(d.stock_margin)}</td>
 <td class="${d.shortage_qty?'neg':'muted'}">${nfmt(d.shortage_qty)}</td><td>${nfmt(d.closing_raw_stock,1)}</td>
 <td><input aria-label="${d.date} sabitle" type="checkbox" id="lock-${d.date}" ${d.locked?'checked':''} onchange="autoInvalidate()"></td>
 <td><span class="status ${d.status}"></span> ${d.shortage_qty?'Sipariş açığı':d.raw_limited_qty?'Hammadde kısıtı':d.safety_shortage_qty?'Emniyet stoku altı':d.status==='yellow'?'Varsayımlı':'Yeterli'}</td></tr>`).join('')+'</tbody>';
 autoEl('autoAcknowledge').checked=false;
}
async function autoHistory(){
 if(AUTO_BUSY)return;
 try{autoBusy(true);const h=await api(`/api/v1/planning/auto/history?${scopeQuery()}`);
 autoEl('autoHistoryInfo').textContent=`${h.sample_count} uygun vardiya · ${h.excluded_count||0} hariç. ${h.warning||''}`;
 const mode=autoEl('autoRateMode').value,baseField=mode==='top5'?'technical_base_capacity':mode==='average'?'normal_base_capacity':'cautious_base_capacity',breakField=mode==='top5'?'technical_break_extra':mode==='average'?'normal_break_extra':'cautious_break_extra';
 if(!Object.keys(h.profiles||{}).length){autoMessage('Uygun geçmiş veri bulunamadı; günlük sağlam üretim kapasitelerini elle girin.',true);return}
 const factor=Number(autoEl('autoSecondFactor').value)/100;
 if(!autoEl('autoSecondFactor').checkValidity())throw new Error('İkinci vardiya verim oranı 0–100 arasında olmalı.');
 for(const [code] of autoPreset){
  let p=h.profiles[code];
  if(code==='double'&&!p&&h.profiles.day8){const b=h.profiles.day8;p={sample_count:b.sample_count,break_sample_count:b.break_sample_count,break_minutes:b.break_minutes*2,[baseField]:Math.floor(b[baseField]*(1+factor)),[breakField]:Math.floor(b[breakField]*(1+factor))}}
  if(!p)continue;
  autoEl('cap-'+code).value=p[baseField];autoEl('break-cap-'+code).value=p[breakField]||0;autoEl('break-minutes-'+code).value=p.break_minutes||0;
  autoEl('break-allow-'+code).checked=(p[breakField]||0)>0;
  autoEl('history-'+code).textContent=`${p.sample_count} vardiya · mola örneği ${p.break_sample_count||0}`;
 }
 autoEl('autoBasis').textContent=`Aynı vardiya süresinden ${mode==='top5'?'son 20 içindeki en iyi 5':mode==='average'?'son 10 normal ortalama':'son 10 alt çeyrek'}; mola dışı temel kapasite ve dönüşümlü mola katkısı ayrıdır. İkinci vardiya verimi %${Math.round(factor*100)}.`;
 autoInvalidate();autoMessage('Kapasite alanları geçmişten dolduruldu; planı oluşturabilirsiniz.');
 }catch(e){autoMessage(e.message,true)}finally{autoBusy(false)}
}
async function autoRestore(){
 if(AUTO_BUSY)return;
 if(AUTO_DIRTY&&AUTO_RUN){autoMessage('Kaydedilmemiş değişiklikler var. Önce yeniden hesaplayın.',true);return}
 try{autoBusy(true);const start=autoEl('autoStart').value,days=Number(autoEl('autoDays').value);
 const run=await api(`/api/v1/planning/auto/latest?${scopeQuery()}&start_date=${start}&days=${days}`);
 if(!run){AUTO_RUN=null;autoEl('autoResult').hidden=true;autoMessage('Bu dönem için kayıtlı taslak yok.');await autoApproved();return}
 AUTO_RUN=run;const b=run.request;
 if(b.minimum_closing_stock!==null&&b.minimum_closing_stock!==undefined)autoEl('autoMinimumStock').value=b.minimum_closing_stock;
 for(const [code] of autoPreset){const opt=b.options.find(x=>x.code===code);autoEl('allow-'+code).checked=code==='day8'||!!opt;if(opt){autoEl('cap-'+code).value=opt.capacity;autoEl('break-cap-'+code).value=opt.break_extra_capacity||0;autoEl('break-minutes-'+code).value=opt.break_minutes||0;autoEl('break-allow-'+code).checked=(opt.break_extra_capacity||0)>0}}
 document.querySelectorAll('#autoWorkdays input').forEach(x=>x.checked=b.workdays.includes(Number(x.value)));
 autoEl('autoPlanned').checked=b.include_planned;autoEl('autoDispatch').value=b.dispatch_timing;autoEl('autoBasis').textContent=b.capacity_note;
 autoEl('autoMode').value=b.planning_mode||'balanced_weekly';
 if(run.result.policy_version!=='materials_stock_v1'){AUTO_RUN=null;AUTO_DIRTY=false;autoEl('autoResult').hidden=true;autoMessage('Önceki kuralla kaydedilmiş plan. Vardiya ve mola kapasitesi için yeniden oluşturun; sabit günleri yeniden seçin. Mevcut onaylı adetler aşağıda görünür.');await autoApproved();return}
 AUTO_DIRTY=false;autoRender();autoMessage('Kayıtlı plan açıldı. Onay öncesinde kaynak verilerin güncelliği yeniden kontrol edilir.');await autoApproved();
 }catch(e){autoMessage(e.message,true)}finally{autoBusy(false)}
}
async function autoApprove(){
 if(AUTO_BUSY||!AUTO_RUN||AUTO_DIRTY)return;
 if(!autoEl('autoAcknowledge').checked){autoMessage('Varsayımları, kapasite ve ekip uygunluğunu kontrol ettiğinizi işaretleyin.',true);return}
 try{autoBusy(true);await api(`/api/v1/planning/auto/${AUTO_RUN.run_id}/approve?${scopeQuery()}`,{method:'POST',body:JSON.stringify({acknowledge_warnings:true})});
 AUTO_RUN.status='approved';autoRender();await loadBoard();await autoApproved();autoMessage('Plan onaylandı; bu dönemin günlük adetleri ve çalışma etiketleri kaydedildi.');
 }catch(e){autoMessage(e.message,true)}finally{autoBusy(false)}
}
async function autoApproved(){
 const start=autoEl('autoStart').value,end=addDays(start,Number(autoEl('autoDays').value)-1);
 const data=await api(`/api/v1/planning/auto/approved-days?${scopeQuery()}&start_date=${start}&end_date=${end}`);
 autoEl('autoActualHint').textContent=data.actual_basis;
 autoEl('autoApprovedTable').innerHTML='<thead><tr><th>Gün</th><th>Onaylanan çalışma</th><th>Planlanan</th><th>Özetlerde gerçekleşen</th><th>Hedefe kalan</th></tr></thead><tbody>'+(data.items.length?data.items.map(r=>`<tr><td>${trDate(r.plan_date)}</td><td>${esc(r.shift_label)}</td><td>${nfmt(r.planned_qty)}</td><td>${r.actual_good===null?'—':nfmt(r.actual_good)}</td><td>${r.remaining_qty===null?'—':nfmt(r.remaining_qty)}</td></tr>`).join(''):'<tr><td colspan="5" class="left muted">Bu dönemde onaylanmış otomatik plan yok.</td></tr>')+'</tbody>';
}
function autoPeriodChanged(){AUTO_RUN=null;AUTO_DIRTY=false;autoEl('autoResult').hidden=true;autoEl('autoApprove').disabled=true;autoEl('autoApprovedTable').innerHTML='';autoMessage('Dönem değişti. Kayıtlı planı açabilir veya yeni plan oluşturabilirsiniz.')}
function autoInit(){
 autoEl('autoStart').value=isoToday();
 autoEl('autoWorkdays').innerHTML=dayNames.map((name,i)=>`<label><input type="checkbox" value="${i}" ${i<5?'checked':''} onchange="autoInvalidate()">${name}</label>`).join('');
 autoEl('autoOptions').innerHTML=autoPreset.map(([code,label])=>`<div class="field auto-option"><span><input type="checkbox" id="allow-${code}" ${code!=='double'?'checked':''} ${code==='day8'?'disabled':''} onchange="autoInvalidate()"> <b>${label}</b></span><label>Mola dışı sağlam kapasite<input aria-label="${label} mola dışı sağlam kapasite" id="cap-${code}" type="number" min="${code==='day8'?1:0}" max="10000000" step="1" placeholder="Sağlam adet/gün" onchange="autoEl('autoBasis').textContent='Elle düzenlenmiş kapasite ve mola katkısı';autoInvalidate()"></label><label><input type="checkbox" id="break-allow-${code}" onchange="autoInvalidate()"> Gerektiğinde dönüşümlü mola</label><div class="break-fields"><label>Mola katkısı<input id="break-cap-${code}" type="number" min="0" max="1000000" step="1" value="0" onchange="autoInvalidate()"></label><label>Mola dk<input id="break-minutes-${code}" type="number" min="0" max="240" step="1" value="0" onchange="autoInvalidate()"></label></div><small class="muted" id="history-${code}">Geçmiş veri yüklenmedi</small></div>`).join('');
 showPanel('auto');
 loadConfig().then(()=>{
  const days=CONFIG.workdays??[0,1,2,3,4];
  autoEl('autoWorkdays').querySelectorAll('input').forEach(x=>x.checked=days.includes(Number(x.value)));
  return autoRestore();
 }).catch(e=>autoMessage(e.message,true));
}
autoInit();
