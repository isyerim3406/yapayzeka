#!/usr/bin/env python3
"""Install Fiction Studio comic extension into existing Oracle Flask app. No backups."""
import ast
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import urllib.request

ROOT=Path('/home/ubuntu/fiction-gpu')
RAW='https://raw.githubusercontent.com/isyerim3406/yapayzeka/main/fiction-comic/'
MARK='# FICTION_COMIC_EXTENSION_V1'
JS_MARK='/* FICTION_COMIC_UI_V1 */'
SCRIPT=r'''
/* FICTION_COMIC_UI_V1 */
(function(){
  const button=document.createElement('button');
  button.type='button';
  button.textContent='Comic Book Hazırla';
  button.style.cssText='margin:8px;padding:10px 14px;border-radius:9px;background:#355886;color:white;border:0;font-weight:600;cursor:pointer';
  (promptBox.parentElement||document.body).appendChild(button);
  const section=document.createElement('section');
  section.style.cssText='margin:14px 2px;padding:12px;border:1px solid #5555;border-radius:12px';
  section.innerHTML='<strong>Biten İşler · Comic PDF</strong><div class="fiction-comic-results" style="padding-top:8px"></div>';
  (messages.parentElement||document.body).appendChild(section);
  const box=section.querySelector('.fiction-comic-results');

  async function refreshComics(){
    try {
      const data=await api('/api/comics');
      box.replaceChildren();
      for(const item of data.items||[]){
        const row=document.createElement('div');
        row.style.cssText='padding:6px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center';
        const label=document.createElement('span');
        label.textContent='Comic '+item.id.slice(0,7)+' · '+item.status+' · '+(item.panel_count||0)+' panel';
        row.appendChild(label);
        if(item.pdf_url){
          const link=document.createElement('a');
          link.href=item.pdf_url;
          link.target='_blank';
          link.rel='noopener';
          link.textContent='PDF Aç';
          link.style.textDecoration='underline';
          row.appendChild(link);
          const download=document.createElement('a');
          download.href=item.pdf_url;
          download.download='Comic-'+item.id+'.pdf';
          download.textContent='İndir';
          row.appendChild(download);
        }
        if(item.status==='error'){
          const err=document.createElement('small');
          err.textContent=item.error||'İş başarısız';
          row.appendChild(err);
        }
        box.appendChild(row);
      }
      if(!(data.items||[]).length)box.textContent='Henüz tamamlanan comic yok.';
    }catch(e){box.textContent='Liste alınamadı: '+e.message;}
  }
  async function gpuReady(){
    let status=await api('/api/gpu/status');
    if(status.kaggle==='RUNNING'&&!status.stop_requested)return;
    statusEl.textContent='Kaggle GPU başlatılıyor…';
    await api('/api/gpu/start',{method:'POST',headers});
    for(let i=0;i<140;i++){
      await new Promise(resolve=>setTimeout(resolve,6000));
      status=await api('/api/gpu/status');
      if(status.kaggle==='RUNNING'&&!status.stop_requested)return;
      if(['ERROR','CANCELLED'].includes(status.kaggle))throw Error('GPU başlatılamadı');
    }
    throw Error('GPU bekleme süresi aşıldı');
  }
  button.addEventListener('click',async()=>{
    if(button.disabled)return;
    button.disabled=true;
    try{
      await gpuReady();
      const text=promptBox.value.trim();
      const job=await api('/api/comics',{method:'POST',headers,body:JSON.stringify({text})});
      if(text)promptBox.value='';
      statusEl.textContent='Comic hazırlanıyor. PDF Biten İşler bölümünde görünecek.';
      await refreshComics();
    }catch(e){statusEl.textContent='Comic hata: '+e.message;}
    finally{button.disabled=false;}
  });
  refreshComics();
  setInterval(refreshComics,12000);
})();
'''

def retrieve(name):
    req=urllib.request.Request(RAW+name,headers={'User-Agent':'FictionComicInstaller/1.0'})
    with urllib.request.urlopen(req,timeout=35) as response:
        return response.read().decode('utf-8')

def atomic(path,content):
    mode=stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd,tmp=tempfile.mkstemp(prefix='.comic-stage-',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(content)
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def interpreter():
    possibilities=[ROOT/'.venv/bin/python',ROOT/'venv/bin/python',
                   Path('/home/ubuntu/fiction-venv/bin/python')]
    try:
        info=subprocess.check_output(['systemctl','show','fiction-gpu','-p','ExecStart','--value'],text=True,timeout=10)
        for executable in re.findall(r'(/[\w.\-/]+/bin/(?:gunicorn|python\d*(?:\.\d+)?))',info):
            possibilities.insert(0,Path(executable).parent/'python')
    except Exception:
        pass
    for item in possibilities:
        if item.exists():return str(item)
    raise RuntimeError('Fiction Studio Python sanal ortamı bulunamadı; servis ayarlarını kontrol edin.')

def main():
    if not (ROOT/'app.py').is_file() or not (ROOT/'templates/index.html').is_file():
        raise RuntimeError('Fiction Studio klasörü veya dosyaları bulunamadı.')
    print('=== 1/5 KAYNAK DOSYALAR ===',flush=True)
    modules={name:retrieve(name) for name in ('comic_extension.py','story_links.py')}
    for name,content in modules.items():ast.parse(content,filename=name)
    appfile=ROOT/'app.py'
    htmlfile=ROOT/'templates/index.html'
    appcode=appfile.read_text(encoding='utf-8')
    htmlcode=htmlfile.read_text(encoding='utf-8')
    if 'def set_job(' not in appcode or 'def db(' not in appcode or '</script>' not in htmlcode:
        raise RuntimeError('Beklenmeyen web panel yapısı; değişiklik yapılmadı.')
    print('=== 2/5 PAKETLER ===',flush=True)
    python=interpreter()
    subprocess.check_call([python,'-m','pip','install','-q','Pillow>=10,<13','beautifulsoup4>=4.12,<5'],timeout=300)
    print('=== 3/5 MODUL VE ARAYUZ ===',flush=True)
    for name,content in modules.items():atomic(ROOT/name,content)
    if MARK not in appcode:
        updated=appcode+'\n\n'+MARK+'\nfrom comic_extension import register as _register_comic\n_register_comic(app, globals())\n'
        ast.parse(updated,filename='app.py')
        atomic(appfile,updated)
    if JS_MARK not in htmlcode:
        index=htmlcode.rfind('</script>')
        atomic(htmlfile,htmlcode[:index]+'\n'+SCRIPT+'\n'+htmlcode[index:])
    print('=== 4/5 GPU WORKER KONTROL ===',flush=True)
    worker=ROOT/'.persistent-worker/worker.py'
    if worker.exists():
        old=worker.read_text(encoding='utf-8')
        new=old.replace('tokenizer = image_pipe.tokenizer','tokenizer = pipe.tokenizer')
        if new!=old:
            ast.parse(new,filename='worker.py')
            atomic(worker,new)
            print('GPU worker hatası düzeltildi. Etkinleşmesi için yeni GPU oturumu gerekir.',flush=True)
    print('=== 5/5 WEB SERVISI ===',flush=True)
    subprocess.check_call(['sudo','systemctl','restart','fiction-gpu'],timeout=45)
    status=subprocess.check_output(['systemctl','is-active','fiction-gpu'],text=True).strip()
    print('fiction-gpu: '+status,flush=True)
    if status!='active':
        raise RuntimeError('Servis etkin değil. journalctl -u fiction-gpu -n 35')
    print('Telegram için gerekli ise comic_telegram.env içinde COMIC_TELEGRAM_BOT_TOKEN ve COMIC_TELEGRAM_CHAT_ID belirtin.',flush=True)
    print('Web paneli yenileyin. Çalışan eski Kaggle oturumunu bir kez kapatıp tekrar açın.',flush=True)
    print('=== İŞLEM TAMAMLANDI ===',flush=True)

if __name__=='__main__':main()
