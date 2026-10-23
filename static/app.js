const $ = id => document.getElementById(id);
const state = {docs: [], docId: '', current: null, pageRequest: 0, searchRequest: 0, replacing: null, searched: false};
const escapeHTML = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-CourseTrace':'local'}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok) { const error=new Error(body.error || '请求失败，请重试。');error.status=response.status;throw error; }
  return body;
}
let toastTimer;
async function runJob(action,args={}) {
  if(state.job)throw new Error('请等待当前操作完成。');
  state.job='uploading';$('task-status').textContent='正在传送至本机服务…';$('task-progress').removeAttribute('value');$('task-cancel').disabled=true;$('task-dialog').showModal();
  try {
    let job=await api('/api/jobs',{action,args});state.job=job.id;
    while(true){
      if(job.state==='completed')return job.result;
      if(job.state==='failed'||job.state==='cancelled'){const error=new Error(job.error);error.status=job.status;throw error;}
      $('task-cancel').disabled=job.phase==='committing'||job.state==='cancelling';
      $('task-status').textContent=job.phase==='committing'?'正在提交资料…':job.state==='cancelling'?'正在取消…':job.total?`正在解析第 ${job.page} / ${job.total} 页`:'正在准备解析…';
      if(job.total){$('task-progress').max=job.total;$('task-progress').value=job.page;}
      await new Promise(resolve=>setTimeout(resolve,180));job=await api('/api/jobs?id='+encodeURIComponent(state.job));
    }
  }finally{state.job=null;$('task-dialog').close();}
}
async function cancelJob(){if(state.job&&state.job!=='uploading'){try{await api('/api/jobs/cancel',{id:state.job});$('task-cancel').disabled=true;}catch(error){showError(error);}}}
$('task-cancel').onclick=cancelJob;$('task-dialog').addEventListener('cancel',event=>{event.preventDefault();cancelJob();});
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5000); }
function notice(message='') { $('notice').textContent = message; $('notice').hidden = !message; }
function clearReader() { state.pageRequest++; state.current = null; $('reader').hidden = true; $('reader-empty').hidden = false; $('page-image').removeAttribute('src'); }
function showError(error) { toast(error.message); }
const statusLabels = {ready:'已索引',changed:'待更新索引',missing:'原文件已移走',unreadable:'无法读取'};
async function refresh() {
  state.docs = await api('/api/documents');
  $('doc-count').textContent = state.docs.length;
  $('documents').innerHTML = state.docs.map(d => `<div class="doc-item ${state.docId===d.id?'selected':''}"><button class="doc-open" data-open="${d.id}">${escapeHTML(d.title)}</button><div class="doc-meta"><span>${escapeHTML(d.category)}</span><span>${d.page_count} 页</span></div><div class="doc-meta ${d.status==='ready'?'':'state-warning'}">${statusLabels[d.status]}${d.empty_pages ? ` · ${d.empty_pages} 页无文本` : ''}</div><div class="doc-actions">${d.status!=='ready'?`<button data-update="${d.id}">更新索引</button>`:''}${d.managed?`<button data-replace="${d.id}">替换副本</button>`:`<button data-relocate="${d.id}">重新定位</button>`}<button data-remove="${d.id}">移出资料库</button></div></div>`).join('');
  $('all-docs').classList.toggle('active', !state.docId);
  const excluded = state.docs.filter(d => d.status !== 'ready');
  notice(excluded.length ? `${excluded.length} 份资料已变化或不可读取，暂不参与检索。请检查路径并更新索引。` : '');
  if (state.current && !state.docs.some(d => d.id===state.current.document.id && d.sha===state.current.document.sha && d.status==='ready')) clearReader();
}
function libraryLanding() {
  if (!state.docs.length) return;
  $('results-title').textContent = state.docId ? '在选中的资料中检索' : '你的资料已就绪';
  $('result-count').textContent = `${state.docs.length} 份资料`;
  $('results').innerHTML = `<div class="empty"><div class="empty-pages" aria-hidden="true"><span></span><span></span><span>有据<br>可查</span></div><h3>想核对哪个概念？</h3><p>输入资料中的概念或一段原文。<br>也可以点击左侧资料，直接翻阅原页。</p><button id="try-query" class="secondary">试搜「非理想溶液」</button></div>`;
  $('try-query').onclick = () => { $('query').value='非理想溶液'; search().catch(showError); };
}
function highlight(text, query) {
  if (!query.trim()) return escapeHTML(text);
  const safe = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const parts = text.split(new RegExp(`(${safe})`, 'gi'));
  return parts.map((p,i) => i%2 ? `<mark>${escapeHTML(p)}</mark>` : escapeHTML(p)).join('');
}
async function search() {
  const query = $('query').value.trim();
  if (!query) return;
  const request = ++state.searchRequest;
  state.searched = true;
  $('results-title').textContent = '正在检索资料…';
  const result = await api('/api/search?' + new URLSearchParams({q:query, category:$('category').value, doc_id:state.docId}));
  if(request !== state.searchRequest) return;
  $('results-title').textContent = `“${query}” 的原文出处`;
  $('result-count').textContent = result.total>200 ? `${result.total} 页匹配，显示前 200 页` : `${result.total} 页匹配`;
  notice(result.excluded ? `${result.excluded} 份资料已变化或不可读取，已跳过旧索引。` : '');
  if (!result.results.length) {
    $('results').innerHTML = '<div class="empty"><h3>没有找到这段原文</h3><p>试试更短的词、英文术语，或调整来源类型。<br>扫描页可能没有可提取文字；没有命中不代表资料未涉及该知识点。</p></div>';
    return;
  }
  $('results').innerHTML = result.results.map((r,i) => `<article class="result"><button class="result-button" data-result="${i}"><div class="result-top"><span class="badge">${escapeHTML(r.category)}</span><span class="page-pill">PDF 第 ${r.page} 页 ↗</span></div><h3>${escapeHTML(r.title)}</h3><p>${highlight(r.excerpt,query)}</p></button></article>`).join('');
  $('results').querySelectorAll('[data-result]').forEach(button => button.onclick = () => {
    $('results').querySelectorAll('.result').forEach(el=>el.classList.remove('selected'));
    button.closest('.result').classList.add('selected');
    const r=result.results[Number(button.dataset.result)];
    openPage(r.doc_id,r.page,r.sha,query).then(()=>{if(innerWidth<=900)document.querySelector('.reader-panel').scrollIntoView({behavior:'auto'});}).catch(showError);
  });
}
async function openPage(id, page=1, sha='', query='') {
  const request=++state.pageRequest;
  $('page-error').hidden=true;
  $('page-image').removeAttribute('src');
  state.current=null;
  try {
    const result=await api('/api/page?'+new URLSearchParams({id,page,sha,q:query}));
    if (request!==state.pageRequest) return;
    state.current=result;
    const doc=result.document;
    $('reader-empty').hidden=true; $('reader').hidden=false;
    $('source-title').textContent=doc.title; $('source-category').textContent=doc.category;
    $('page-number').value=page; $('page-number').max=doc.page_count;
    $('page-total').textContent=`/ ${doc.page_count} 页`;
    $('prev-page').disabled=page<=1; $('next-page').disabled=page>=doc.page_count;
    $('page-text').textContent=result.text;$('page-text').value=result.text;$('page-text').placeholder='此页没有可提取文本，请核对原页。当前版本未启用 OCR。';
    $('citation-selection').innerHTML='<option value="">仅记录本页出处，不附正文</option>'+result.matches.map((m,i)=>`<option value="${i}">命中 ${i+1}：${escapeHTML(m.text.replace(/\s+/g,' ').slice(0,65))}</option>`).join('');
    $('citation-selection').value=result.matches.length?'0':'';updateSelection();
    $('version-meta').textContent=`${doc.managed?'本地副本':'跟踪原文件'}：${doc.path}\n索引时间：${new Date(doc.indexed_at).toLocaleString()}\nSHA-256：${doc.sha}`;
    const response=await fetch('/api/preview?'+new URLSearchParams({id,page,sha:doc.sha}));
    if (!response.ok) throw new Error((await response.json()).error);
    const blob=await response.blob();
    if(request!==state.pageRequest) return;
    if (state.imageURL) URL.revokeObjectURL(state.imageURL);
    state.imageURL=URL.createObjectURL(blob);
    $('page-image').src=state.imageURL;
    $('page-image').alt=`${doc.title}，PDF 第 ${page} 页原始页面`;
  } catch(error) {
    if (request!==state.pageRequest) return;
    state.current=null;
    $('page-error').textContent=error.message; $('page-error').hidden=false;
    throw error;
  }
}
async function citation(format) {
  if(!state.current) throw new Error('请先打开有效的原始页面。');
  const r=state.current;
  const c=await api('/api/citation',{id:r.document.id,page:r.page,sha:r.document.sha,...(state.selection?{start:state.selection.start,end:state.selection.end}:{})});
  if(format==='copy') { await navigator.clipboard.writeText(c.markdown); toast('引用已复制，包含页码与版本记录。'); return; }
  const blob=new Blob([format==='md'?c.markdown:JSON.stringify(c,null,2)],{type:format==='md'?'text/markdown;charset=utf-8':'application/json;charset=utf-8'});
  const url=URL.createObjectURL(blob), a=document.createElement('a');
  a.href=url; a.download=`${c.title.replace(/[<>:"/\\|?*]/g,'_')}-p${c.pdf_page}.${format}`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  toast('引用已导出。');
}
async function filePayload(file) {
  if(!file) throw new Error('请先选择 PDF 文件。');
  if(file.size>50*1024*1024) throw new Error('单个 PDF 最大支持 50 MB。');
  const data=await new Promise((resolve,reject)=>{const reader=new FileReader(); reader.onload=()=>resolve(reader.result.split(',')[1]); reader.onerror=()=>reject(new Error('文件读取失败。')); reader.readAsDataURL(file);});
  return {filename:file.name,data};
}
async function demo() {
  toast('正在打开示例资料…');
  state.docId=''; $('category').value='';
  const doc=await runJob('demo'); await refresh();
  $('query').value='非理想溶液'; await search(); await openPage(doc.id,2,doc.sha,'非理想溶液');
}
$('search-form').onsubmit=e=>{e.preventDefault(); search().catch(e=>{ $('results-title').textContent='检索未完成'; showError(e); });};
$('category').onchange=()=>{if($('query').value.trim())search().catch(showError);};
$('all-docs').onclick=async()=>{state.docId='';await refresh(); if($('query').value.trim())search().catch(showError); else libraryLanding();};
$('demo-load').onclick=()=>demo().catch(showError); $('empty-demo').onclick=()=>demo().catch(showError);
$('check-updates').onclick=async()=>{try{await refresh(); if(state.searched)await search(); toast('文件状态已检查。');}catch(e){showError(e);}};
$('documents').onclick=async e=>{
  const b=e.target.closest('button'); if(!b)return;
  try {
    if(b.dataset.open) {state.docId=b.dataset.open; await refresh(); await openPage(state.docId); if($('query').value.trim()) await search(); else libraryLanding();}
    if(b.dataset.update) {b.disabled=true; await runJob('reindex',{id:b.dataset.update});await refresh();if(state.searched)await search();toast('索引已更新。');}
    if(b.dataset.relocate){const path=prompt('请输入原文件的新路径：');if(path){const args={id:b.dataset.relocate,path:path.trim().replace(/^"|"$/g,'')};try{await runJob('relocate',args);}catch(error){if(error.status===409&&confirm(error.message+' 是否继续？'))await runJob('relocate',{...args,allow_changed:true});else throw error;}await refresh();if(state.searched)await search();toast('文件位置已更新。');}}
    if(b.dataset.replace) {state.replacing=b.dataset.replace;$('replace-file').value='';$('replace-file').click();}
    if(b.dataset.remove && confirm('从资料库移除此条记录？原文件和已上传副本都会保留。')) {await api('/api/remove',{id:b.dataset.remove});if(state.docId===b.dataset.remove)state.docId='';await refresh();clearReader();if(state.searched)await search();else location.reload();}
  } catch(error){showError(error);} finally {b.disabled=false;}
};
$('replace-file').onchange=async()=>{try{const payload=await filePayload($('replace-file').files[0]);await runJob('import',{...payload,replace_id:state.replacing});await refresh();if(state.searched)await search();toast('副本与索引已更新。');}catch(e){showError(e);}};
$('import-open').onclick=()=>{$('import-error').textContent='';$('import-dialog').showModal();};
$('import-close').onclick=()=>$('import-dialog').close();
document.querySelectorAll('[name=method]').forEach(r=>r.onchange=()=>{$('upload-group').hidden=r.value!=='upload';$('path-group').hidden=r.value!=='path';});
$('import-form').onsubmit=async e=>{
  e.preventDefault();$('import-error').textContent='';$('import-submit').disabled=true;$('import-submit').textContent='正在解析与建立索引…';
  try {
    const method=document.querySelector('[name=method]:checked').value;
    let payload=method==='upload'?await filePayload($('pdf-file').files[0]):{path:$('local-path').value.trim().replace(/^"|"$/g,'')};
    if(method==='path'&&!payload.path)throw new Error('请输入完整的 PDF 文件路径。');
    $('import-dialog').close();
    const doc=await runJob('import',{...payload,title:$('import-name').value,category:$('import-category').value});
    $('import-dialog').close();$('import-form').reset();$('upload-group').hidden=false;$('path-group').hidden=true;
    await refresh(); libraryLanding();await openPage(doc.id);
    toast(doc.empty_pages?`已导入；${doc.empty_pages} 页没有可提取文本，可查看原页。`:'资料已导入，可以开始检索。');
  }catch(error){$('import-error').textContent=error.message;if(!$('import-dialog').open)$('import-dialog').showModal();}finally{$('import-submit').disabled=false;$('import-submit').textContent='导入并建立索引';}
};
function navigate(delta){if(state.current)openPage(state.current.document.id,state.current.page+delta,state.current.document.sha,state.current.query).catch(showError);}
$('prev-page').onclick=()=>navigate(-1);$('next-page').onclick=()=>navigate(1);
$('page-number').onchange=()=>{if(state.current){const n=Number($('page-number').value);if(!Number.isInteger(n)||n<1||n>state.current.document.page_count){$('page-number').value=state.current.page;toast('请输入此 PDF 范围内的整数页码。');return;}openPage(state.current.document.id,n,state.current.document.sha).catch(showError);}};
function updateSelection(){const choice=$('citation-selection').value;state.selection=choice===''?null:state.current.matches[Number(choice)];$('citation-preview').textContent=state.selection?.text||'';$('citation-hint').textContent=state.selection?'导出下面的原文片段。它属于页面局部，请先核对原页。':'仅导出资料名称、PDF 页码与版本，不自动截取页面开头。';}
$('citation-selection').onchange=updateSelection;
$('use-selection').onclick=()=>{if(!state.current)return;const field=$('page-text');const start=[...field.value.slice(0,field.selectionStart)].length,end=[...field.value.slice(0,field.selectionEnd)].length;if(start===end||end-start>10000){toast('请在提取文本中选中 1–10000 个字符。');return;}state.selection={start,end,text:field.value.slice(field.selectionStart,field.selectionEnd)};$('citation-selection').value='';$('citation-preview').textContent=state.selection.text;$('citation-hint').textContent='已选择自定义原文片段，导出内容如下。';};
$('copy-citation').onclick=()=>citation('copy').catch(showError);$('export-md').onclick=()=>citation('md').catch(showError);$('export-json').onclick=()=>citation('json').catch(showError);
refresh().then(libraryLanding).catch(showError);
