(() => {
  const qsa=(s,r=document)=>[...r.querySelectorAll(s)];
  const num=v=>Number(v);

  function setupSortable(table){
    if(!table)return;
    const tbody=table.tBodies[0]; let key=null,dir='asc';
    qsa('th[data-sort]',table).forEach(th=>th.addEventListener('click',()=>{
      const k=th.dataset.sort; if(key===k)dir=dir==='asc'?'desc':'asc'; else {key=k;dir=(k==='rank'||k==='player'||k==='tier'||k==='course'||k==='holder')?'asc':'desc';}
      qsa('th[data-sort]',table).forEach(x=>x.classList.remove('sort-asc','sort-desc')); th.classList.add(dir==='asc'?'sort-asc':'sort-desc');
      const rows=qsa('tbody tr',table).filter(r=>r.classList.contains('data-row')||r.classList.contains('course-row'));
      rows.sort((a,b)=>{let av=a.dataset[k]??'',bv=b.dataset[k]??''; let c;if(['player','tier','course','holder'].includes(k))c=av.localeCompare(bv,'ja');else c=num(av)-num(bv);return dir==='asc'?c:-c;});
      rows.forEach(r=>tbody.appendChild(r));
    }));
  }
  setupSortable(document.getElementById('ranking-table')); setupSortable(document.getElementById('courses-table'));

  const psearch=document.getElementById('player-search'),tfilter=document.getElementById('tier-filter');
  if(psearch&&tfilter){const apply=()=>qsa('#ranking-table .data-row').forEach(r=>{const ok=(!psearch.value.trim()||r.dataset.search.includes(psearch.value.trim().toLowerCase()))&&(!tfilter.value||r.dataset.tier===tfilter.value);r.style.display=ok?'':'none';});psearch.addEventListener('input',apply);tfilter.addEventListener('change',apply);}
  const csearch=document.getElementById('course-search'); if(csearch)csearch.addEventListener('input',()=>{const q=csearch.value.trim().toLowerCase();qsa('#courses-table .course-row').forEach(r=>r.style.display=(!q||r.dataset.search.includes(q))?'':'none');});

  qsa('.dist-tab').forEach(btn=>btn.addEventListener('click',()=>{qsa('.dist-tab').forEach(x=>x.classList.remove('active'));btn.classList.add('active');qsa('.distribution').forEach(x=>x.classList.add('hidden'));document.getElementById(`dist-${btn.dataset.target}`)?.classList.remove('hidden');}));
  qsa('.lang-btn').forEach(btn=>btn.addEventListener('click',()=>{const lang=btn.dataset.lang;document.documentElement.lang=lang;qsa('[data-en][data-ja]').forEach(el=>el.textContent=lang==='ja'?el.dataset.ja:el.dataset.en);qsa('.lang-btn').forEach(x=>x.classList.toggle('active',x===btn));}));

  const simDataEl=document.getElementById('player-simulator-data');
  if(simDataEl){
    const player=JSON.parse(simDataEl.textContent); const select=document.getElementById('sim-course'),input=document.getElementById('sim-time'); let courses={};
    const recMap=new Map(player.records.map(r=>[r.course,r]));
    const weights=(()=>{const arr=[];for(let i=1;i<=30;i++){const raw=1/(1+Math.exp(.18*(i-20)));arr.push(raw)}const a=arr[0],z=arr[29];return arr.map((v,i)=>i===0?1:i===29?.5:.5+.5*(v-z)/(a-z));})();
    const fmt=ms=>{if(!ms)return'—';const m=Math.floor(ms/60000),s=Math.floor((ms%60000)/1000),x=ms%1000;return `${m}:${String(s).padStart(2,'0')}.${String(x).padStart(3,'0')}`};
    const parse=s=>{const m=s.trim().match(/^(\d+):([0-5]?\d)(?:\.(\d{1,3}))?$/);if(!m)return null;return +m[1]*60000 + +m[2]*1000 + +((m[3]||'').padEnd(3,'0'));};
    const grade=r=>Math.abs(r-100)<1e-9?'SS':r>=99.5?'S+':r>=99?'S':r>=98?'A+':r>=96.5?'A':r>=95?'B+':r>=92.5?'B':r>=90?'C+':'C';
    const rating=p=>{if(p<=100){const h=x=>Math.log((x+50)/(105-x));return 100+900*(h(p)-h(0))/(h(100)-h(0));}const x=p-100,g=v=>Math.log((v+26.25)/(410-v));return 1000+600*(g(x)-g(0))/(g(10)-g(0));};
    const calcRating=records=>{const top=[...records].sort((a,b)=>b.adjusted-a.adjusted||a.course.localeCompare(b.course,'ja')).slice(0,30);const p=top.reduce((s,r,i)=>s+r.adjusted*weights[i],0)/weights.reduce((a,b)=>a+b,0);return {rating:rating(p),top};};
    const set=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v};
    function currentCourse(){return courses[select.value]}
    function setCurrent(){const c=currentCourse(),r=recMap.get(select.value);set('sim-current-pb',r?fmt(r.time):'—');set('sim-current-rank',r?`#${r.rank}`:'—');set('sim-current-grade',r?r.grade:'—');set('sim-difficulty',c?c.difficulty.toFixed(3):'—');if(r)input.value=fmt(r.time);else input.value='';render();}
    function render(){const c=currentCourse();if(!c)return;const old=recMap.get(select.value);const ms=parse(input.value);const msg=document.getElementById('sim-message'),status=document.getElementById('sim-status');msg.textContent='';status.innerHTML='';if(ms==null){['sim-rank','sim-raw','sim-grade','sim-adjusted','sim-best30'].forEach(x=>set(x,'—'));return;}
      if(old&&ms>=old.time){msg.textContent=ms===old.time?'Same as current PB — no update.':'Slower than current PB — no update.';set('sim-new-rating',player.rating.toFixed(2));set('sim-delta','+0.00');set('sim-rank',`#${old.rank}`);set('sim-raw',old.raw.toFixed(3));set('sim-grade',old.grade);set('sim-adjusted',old.adjusted.toFixed(3));const oldSorted=[...player.records].sort((a,b)=>b.adjusted-a.adjusted||a.course.localeCompare(b.course,'ja'));const pos=oldSorted.findIndex(r=>r.course===old.course)+1;set('sim-best30',pos&&pos<=30?`#${pos}`:'OUT');return;}
      const rank=1+c.times.filter(t=>t<ms).length;if(!old&&rank>100){msg.textContent='Outside the top 100 — this time would not enter the rating data.';set('sim-rank',`#${rank}`);set('sim-new-rating',player.rating.toFixed(2));set('sim-delta','+0.00');set('sim-raw','—');set('sim-grade','—');set('sim-adjusted','—');set('sim-best30','OUT');return;}
      const wr=Math.min(c.wr,ms),raw=100*wr/ms,gr=grade(raw),adjusted=raw*c.multiplier;const newRec={course:select.value,time:ms,rank,raw,grade:gr,adjusted};let records=player.records.filter(r=>r.course!==select.value);records.push(newRec);const result=calcRating(records);const oldTop=calcRating(player.records).top;const oldPos=oldTop.findIndex(r=>r.course===select.value)+1,newPos=result.top.findIndex(r=>r.course===select.value)+1;const delta=result.rating-player.rating;
      set('sim-rank',`#${rank}`);set('sim-raw',raw.toFixed(3));set('sim-grade',gr);set('sim-adjusted',adjusted.toFixed(3));set('sim-new-rating',result.rating.toFixed(2));set('sim-delta',`${delta>=0?'+':''}${delta.toFixed(2)}`);document.getElementById('sim-delta').className=delta>0?'positive':delta<0?'negative':'neutral';set('sim-best30',`${oldPos&&oldPos<=30?'#'+oldPos:'OUT'} → ${newPos&&newPos<=30?'#'+newPos:'OUT'}`);
      const pills=[];pills.push('<span>PB UPDATE</span>');if(ms<c.wr)pills.push('<span>NEW #1</span>');else if(ms===c.wr)pills.push('<span>TIED #1</span>');if((!oldPos||oldPos>30)&&newPos&&newPos<=30)pills.push('<span>NEW BEST 30</span>');else if(oldPos&&oldPos<=30&&newPos&&newPos<=30)pills.push('<span>BEST 30 RETAINED</span>');else if(!newPos||newPos>30)pills.push('<span class="warn">OUTSIDE BEST 30</span>');if((!oldPos||oldPos>30)&&newPos&&newPos<=30){const dropped=oldTop[29];if(dropped&&dropped.course!==select.value)pills.push(`<span>DROPS ${dropped.course}</span>`)}status.innerHTML=pills.join('');
    }
    fetch('../data/simulator_courses.json').then(r=>r.json()).then(data=>{courses=data;Object.keys(courses).sort((a,b)=>a.localeCompare(b,'ja')).forEach(name=>{const o=document.createElement('option');o.value=name;o.textContent=name;select.appendChild(o)});const first=player.records[0]?.course||Object.keys(courses)[0];if(first)select.value=first;setCurrent();});
    select.addEventListener('change',setCurrent);input.addEventListener('input',render);qsa('[data-offset]').forEach(b=>b.addEventListener('click',()=>{const r=recMap.get(select.value),base=r?.time??parse(input.value);if(base==null)return;input.value=fmt(Math.max(1,base+Number(b.dataset.offset)));render()}));document.getElementById('sim-wr')?.addEventListener('click',()=>{const c=currentCourse();if(c){input.value=fmt(c.wr);render()}});
  }
})();
