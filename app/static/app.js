'use strict';
const $ = id => document.getElementById(id);
let authenticated = false, selected = null, currentResult = null, timer = null, documents = [], offset = 0, totalDocuments = 0, loading = false, generation = 0;
const labels = {UPLOADED:'Queued',VALIDATING:'Validating file',OCR_PROCESSING:'OCR processing',OCR_RETRY_PENDING:'Retry scheduled',OCR_FAILED:'OCR failed',AI_EXTRACTION_PENDING:'Ready for AI extraction',HUMAN_REVIEW:'Human review required'};
function notice(message='') { $('notice').textContent = message; }
async function api(path, options={}) {
  const response = await fetch('/api/v1' + path, {...options, credentials: 'same-origin', headers: {'X-Requested-With':'LedgerLens', ...options.headers}});
  if (!response.ok) {
    if(response.status===401 && authenticated)showLogin();
    let body; try { body=await response.json(); } catch { body={detail:'Request failed.'}; }
    throw new Error(typeof body.detail==='string' ? body.detail : 'Please check your request and try again.');
  }
  return response.json();
}
function make(tag,text,className) { const el=document.createElement(tag); el.textContent=text; if(className)el.className=className; return el; }
function renderDocuments(total) {
  $('document-list').replaceChildren();
  if(!documents.length) $('document-list').append(make('p','No invoices yet. Add your first document above.','muted'));
  for(const doc of documents) {
    const row=make('button','','document-row'+(selected===doc.id?' selected':''));
    row.append(make('strong',doc.file_name),make('span',`${doc.page_count} page${doc.page_count===1?'':'s'} · ${labels[doc.status]||doc.status}`));
    row.onclick=()=>selectDocument(doc.id); $('document-list').append(row);
  }
  totalDocuments=total;
  $('load-more').hidden=offset+documents.length>=total;
  $('previous').hidden=offset===0;
  renderGrid();
}
function renderGrid() {
  const search=$('grid-search').value.trim().toLowerCase(), filter=$('grid-filter').value;
  const visible=documents.filter(doc=>{
    if(filter==='completed'&&!doc.invoice_summary)return false;
    if(filter==='review'&&doc.status!=='HUMAN_REVIEW')return false;
    return [doc.file_name,...Object.values(doc.invoice_summary||{})].join(' ').toLowerCase().includes(search);
  });
  $('invoice-rows').replaceChildren();
  for(const doc of visible) {
    const row=document.createElement('tr'), values=doc.invoice_summary||{};
    if(doc.id===selected)row.className='selected';
    const fields=[offset+documents.indexOf(doc)+1,doc.file_name,...['invoice_date','invoice_number','vendor','customer','phone','amount','tax','balance'].map(key=>values[key]||'—')];
    fields.forEach(value=>row.append(make('td',value)));
    const status=make('td','');status.append(make('span',labels[doc.status]||doc.status,'pill'));row.append(status);
    const action=make('td',''),button=make('button','View','quiet');
    button.setAttribute('aria-label','View '+doc.file_name);
    button.onclick=async()=>{await selectDocument(doc.id);$('selected-detail').scrollIntoView({behavior:'smooth',block:'start'});};
    action.append(button);row.append(action);$('invoice-rows').append(row);
  }
  if(!visible.length){const row=make('tr',''),cell=make('td',documents.length?'No documents match this page’s filters.':'Upload an invoice to see its extracted details here.');cell.colSpan=12;row.append(cell);$('invoice-rows').append(row);}
  $('grid-count').textContent=`${visible.length} shown · Documents ${totalDocuments?offset+1:0}–${offset+documents.length} of ${totalDocuments}`;
  $('grid-previous').disabled=offset===0;$('grid-next').disabled=offset+documents.length>=totalDocuments;
}
async function refresh() {
  if(!authenticated||loading)return;
  loading=true; const epoch=generation;
  try {
    const [list,metrics]=await Promise.all([api(`/documents?limit=50&offset=${offset}`),api('/ocr/metrics')]);
    if(epoch!==generation)return;
    documents=list.items; renderDocuments(list.total);
    $('metric-total').textContent=metrics.total_documents;
    $('metric-queue').textContent=metrics.queue_size+metrics.processing;
    $('metric-completed').textContent=metrics.completed;
    $('metric-review').textContent=metrics.manual_review;
    if(selected)await refreshDetail(epoch);
  } finally { loading=false; }
}
async function selectDocument(id) {
  selected=id; currentResult=null; $('results').hidden=true;
  $('empty-detail').hidden=true; $('selected-detail').hidden=false;
  $('document-name').textContent=documents.find(d=>d.id===id)?.file_name||'Invoice';
  renderDocuments(totalDocuments);
  try { await refreshDetail(generation); } catch(error) { notice(error.message); }
}
async function refreshDetail(epoch) {
  const id=selected; const status=await api('/ocr/status/'+id);
  if(id!==selected||epoch!==generation)return;
  $('stage').textContent=labels[status.stage]||status.stage;
  const stages=[['UPLOADED','File uploaded'],['VALIDATING','File validated'],['OCR_PROCESSING','Read text and layout'],['OCR_COMPLETED','OCR completed'],['AI_EXTRACTION_PENDING','AI extraction pending']];
  const seen=new Set(status.history.map(e=>e.status)); $('timeline').replaceChildren();
  for(const [key,label] of stages) {
    const done=key==='UPLOADED'||(key==='VALIDATING'?seen.has('OCR_PROCESSING'):key==='OCR_PROCESSING'?seen.has('OCR_COMPLETED'):key==='OCR_COMPLETED'?status.status==='completed':false);
    const current=(key==='VALIDATING'&&status.stage==='VALIDATING')||(key==='OCR_PROCESSING'&&status.status==='processing'&&status.stage==='OCR_PROCESSING');
    $('timeline').append(make('li',label,done?'done':current?'current':''));
  }
  $('progress-info').textContent=`${status.pages_processed} of ${status.page_count} pages processed · ${status.attempts} attempts`;
  $('warnings').replaceChildren();
  for(const warning of [...status.warnings,...(status.error?[status.error]:[])]) $('warnings').append(make('div',warning));
  if(status.review_required) $('warnings').prepend(make('strong','Review required before AI extraction.'));
  $('retry').hidden=status.status!=='failed';
  if(status.status==='completed'&&!currentResult) {
    const result=await api('/ocr/result/'+id);
    if(id!==selected||epoch!==generation)return;
    currentResult=result;
    $('result-pages').textContent=result.page_count;
    $('result-confidence').textContent=result.confidence===null?'Not scored':Math.round(result.confidence*100)+'%';
    $('result-time').textContent=result.processing_time.toFixed(1)+'s';
    $('page-select').replaceChildren(...result.pages.map((p,i)=>{const option=make('option','Page '+p.page_number);option.value=i;return option;}));
    $('results').hidden=false; renderPage();
  }
}
function renderPage() {
  if(!currentResult)return;
  const page=currentResult.pages[Number($('page-select').value)||0];
  $('extracted-text').textContent=page.text||'No readable text detected on this page.';
  const details=currentResult.invoice_summary||{};
  $('layout-data').replaceChildren(make('p','Invoice details across all pages. Please verify detected values against the original.','muted'));
  const grid=make('dl','','basic-details');
  const fields=[['customer','Customer'],['amount','Final amount'],['vendor','From / supplier'],['invoice_number','Invoice number'],['invoice_date','Invoice date'],['due_date','Due date'],['subtotal','Subtotal'],['tax','Tax amount'],['balance','Balance due'],['currency','Currency'],['phone','Contact phone'],['email','Email'],['tax_id','GST / tax ID'],['purchase_order','Purchase order']];
  for(const [key,label] of fields){
    const card=make('div','',key==='amount'?'basic-field final-amount':'basic-field');
    card.append(make('dt',label),make('dd',details[key]||'Not detected',details[key]?'':'not-detected'));
    grid.append(card);
  }
  $('layout-data').append(grid);
}
function showLogin() {
  generation++; clearInterval(timer); authenticated=false; selected=null; currentResult=null; documents=[]; offset=0;
  $('workspace').hidden=true; $('connection').hidden=false; $('disconnect').hidden=true;
  $('selected-detail').hidden=true; $('empty-detail').hidden=false;
  $('document-list').replaceChildren(); $('invoice-rows').replaceChildren(); $('extracted-text').textContent=''; $('layout-data').replaceChildren();
}
async function enterWorkspace() {
  authenticated=true;
  await refresh();
  if(!authenticated)return;
  $('connection').hidden=true; $('workspace').hidden=false; $('disconnect').hidden=false;
  clearInterval(timer); timer=setInterval(()=>refresh().catch(e=>notice(e.message)),2500);
}
$('connect-form').onsubmit=async event=>{
  event.preventDefault(); notice(); $('login-submit').disabled=true;
  try {
    await api('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('login-name').value.trim(),password:$('login-password').value})});
    $('login-password').value=''; await enterWorkspace();
  } catch(error){showLogin();notice(error.message);}
  finally{$('login-submit').disabled=false;}
};
$('disconnect').onclick=async()=>{
  try {await api('/auth/logout',{method:'POST'});showLogin();notice();$('login-password').value='';}
  catch(error){notice(error.message);}
};
async function restoreSession() {
  $('login-submit').disabled=true;
  try {
    const response=await fetch('/api/v1/auth/session',{credentials:'same-origin'});
    if(response.ok)await enterWorkspace();
  } catch(error){notice('Unable to connect. Please try signing in again.');}
  finally{$('login-submit').disabled=false;}
}
restoreSession();
async function upload(file) {
  if(!file)return;
  if(!/\.(pdf|jpe?g|png)$/i.test(file.name)||file.size>10*1024*1024){notice('Choose a PDF, JPG or PNG file up to 10 MB.');return;}
  const epoch=generation;$('file-input').disabled=true;$('upload-state').textContent='Uploading and validating…';notice();
  try {const form=new FormData();form.append('file',file);const doc=await api('/documents',{method:'POST',body:form});if(epoch!==generation)return;$('upload-state').textContent=doc.duplicate?'This invoice is already in your workspace.':'Uploaded. OCR is queued.';await refresh();await selectDocument(doc.document_id);}
  catch(error){notice(error.message);$('upload-state').textContent='Upload failed.';}
  finally{$('file-input').disabled=false;$('file-input').value='';}
}
$('file-input').onchange=event=>upload(event.target.files[0]);
for(const event of ['dragenter','dragover']) $('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.add('dragging');});
for(const event of ['dragleave','drop']) $('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');});
$('drop-zone').addEventListener('drop',e=>{if(!$('file-input').disabled)upload(e.dataTransfer.files[0]);});
$('refresh').onclick=()=>refresh().catch(e=>notice(e.message));
$('load-more').onclick=()=>{if(loading)return;offset+=50;refresh().catch(e=>notice(e.message));};
$('previous').onclick=()=>{if(loading)return;offset=Math.max(0,offset-50);refresh().catch(e=>notice(e.message));};
$('grid-search').oninput=renderGrid;
$('grid-filter').onchange=renderGrid;
$('grid-previous').onclick=()=>$('previous').onclick();
$('grid-next').onclick=()=>$('load-more').onclick();
$('retry').onclick=async()=>{ $('retry').disabled=true;try{await api('/ocr/process',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({document_id:selected})});currentResult=null;await refresh();}catch(e){notice(e.message);}finally{$('retry').disabled=false;} };
$('page-select').onchange=renderPage;
for(const mode of ['text','layout']) $(mode+'-tab').onclick=()=>{$('extracted-text').hidden=mode!=='text';$('layout-data').hidden=mode!=='layout';$('text-tab').classList.toggle('active',mode==='text');$('layout-tab').classList.toggle('active',mode==='layout');$('page-select').hidden=mode==='layout';};
function download(href,filename){const anchor=document.createElement('a');anchor.href=href;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();}
$('download-json').onclick=()=>{if(!currentResult)return;const url=URL.createObjectURL(new Blob([JSON.stringify(currentResult,null,2)],{type:'application/json'}));download(url,`ocr-${selected}.json`);setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('download-original').onclick=async()=>{try{const data=await api(`/documents/${selected}/download-url`);download(data.url,'invoice');}catch(e){notice(e.message);}};
