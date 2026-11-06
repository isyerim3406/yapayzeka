"""Fiction Studio Comic v1: Cydonia -> RealVisXL -> mobile PDF.

Installed into the existing Flask app with register(app, app_globals).
Processes jobs on Oracle, submits tasks to the existing Kaggle bridge.
"""
import ast
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Blueprint, abort, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps

from story_links import StoryFetchError, collect_links, parse_user_input

BASE = Path(__file__).resolve().parent
BRIDGE = 'http://127.0.0.1:8790'
OUTPUT_ROOT = BASE / 'comic_output'
LOG = logging.getLogger('fiction.comics')
CHAT_TIMEOUT = 1200
SCENE_TIMEOUT = 1800
MAX_PANELS = 72
MAX_SOURCE_CHARS = 140_000


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def migrate(db):
    OUTPUT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    OUTPUT_ROOT.chmod(0o700)
    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS comic_projects(
            id TEXT PRIMARY KEY, source_kind TEXT NOT NULL,
            status TEXT NOT NULL, panel_count INTEGER DEFAULT 0,
            page_count INTEGER DEFAULT 0, error TEXT DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        ''')
        # Daemon workers cannot survive gunicorn restart. Unblock stale jobs.
        conn.execute("UPDATE comic_projects SET status='error', error='Web servisi yeniden başladı; işlem kesildi.' WHERE status IN ('queued','running')")
        conn.execute("UPDATE jobs SET status='error',detail='Sunucu yeniden başladı; görev kesildi.' WHERE kind='comic' AND status NOT IN ('complete','error')")


def bridge_submit(task):
    r = requests.post(BRIDGE+'/admin/submit', json=task, timeout=(5, 25))
    r.raise_for_status()
    result = r.json()
    if not result.get('job_id'):
        raise RuntimeError('GPU köprüsü görev numarası döndürmedi.')
    return str(result['job_id'])


def bridge_wait(task_id, timeout):
    cutoff = time.monotonic() + timeout
    while time.monotonic() < cutoff:
        r = requests.get(BRIDGE+'/admin/status/'+task_id, timeout=(5, 20))
        r.raise_for_status()
        result = r.json()
        status = result.get('status')
        if status in ('error', 'failed'):
            raise RuntimeError(str(result.get('error') or result.get('response'))[:500])
        if status == 'complete':
            value = result.get('response', '')
            if isinstance(value, str) and value.startswith('[GPU HATASI]'):
                raise RuntimeError(value[:500])
            return value
        time.sleep(3)
    raise TimeoutError('GPU görevi süre aşımına uğradı; Kaggle bağlantısını denetleyin.')


def bridge_image_route():
    """Discover existing local bridge's scene image GET endpoint, no hard-coded URL guesses."""
    filename = BASE / 'persistent_bridge.py'
    tree = ast.parse(filename.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'get_scene_image':
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
                    return str(dec.args[0].value)
    raise RuntimeError('GPU köprüsünde görsel indirme adresi bulunamadı.')


def image_bytes(bridge_id, index):
    path = bridge_image_route()
    path = path.replace('<job_id>', bridge_id).replace('<int:scene_index>', str(index))
    path = path.replace('<scene_index>', str(index))
    if '<' in path or not path.startswith('/'):
        raise RuntimeError('Bilinmeyen köprü görsel adresi: '+path)
    r = requests.get(BRIDGE+path, timeout=(5, 40))
    r.raise_for_status()
    content = r.content
    if content[:8] != b'\x89PNG\r\n\x1a\n':
        raise RuntimeError('Köprü PNG görseli döndürmedi.')
    if len(content) > 16_000_000:
        raise RuntimeError('Görsel beklenenden çok büyük.')
    return content


def parse_json_output(response):
    if isinstance(response, dict) and 'response' in response:
        response = response['response']
    if isinstance(response, dict):
        obj = response
    else:
        raw = str(response).strip()
        if raw.startswith('[GPU HATASI]'):
            raise RuntimeError(raw[:500])
        a = raw.find('{')
        b = raw.rfind('}')
        if a == -1 or b == -1 or b <= a:
            raise ValueError('Cydonia geçerli JSON planı oluşturamadı.')
        obj = json.loads(raw[a:b+1])
    if not isinstance(obj, dict) or not isinstance(obj.get('panels'), list):
        raise ValueError('Cydonia panel listesi döndürmedi.')
    panels = []
    for panel in obj['panels'][:10]:
        if not isinstance(panel, dict):
            continue
        visual = str(panel.get('visual_prompt') or panel.get('description') or '')[:700].strip()
        if len(visual) < 20:
            continue
        dialog = panel.get('dialogue') or []
        if isinstance(dialog, str):
            dialog = [{'speaker':'', 'text':dialog}]
        clean_dialog = []
        if isinstance(dialog, list):
            for line in dialog[:3]:
                if isinstance(line, dict):
                    text = str(line.get('text', '')).strip()[:190]
                    who = str(line.get('speaker', '')).strip()[:35]
                    if text:
                        clean_dialog.append({'speaker':who,'text':text})
        panels.append({'visual_prompt':visual,'dialogue':clean_dialog,
                       'caption':str(panel.get('caption') or '')[:220]})
    if not panels:
        raise ValueError('Cydonia kullanılabilir görsel sahne oluşturamadı.')
    return panels


def split_story(story, maxlen=5000):
    chunks = []
    text = story.strip()
    while text:
        if len(text) <= maxlen:
            chunks.append(text)
            break
        cut = text.rfind('\n', maxlen//2, maxlen)
        if cut < maxlen//2:
            cut = text.rfind('. ', maxlen//2, maxlen)
            cut += 1 if cut != -1 else 0
        if cut < maxlen//2:
            cut = maxlen
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return chunks


def plan_panels(story, progress, max_panels=60):
    chunks = split_story(story)
    if len(chunks) > 24:
        raise ValueError('Hikâye çok uzun; en fazla 24 metin bölümü destekleniyor.')
    panels = []
    character_context = ''
    for ix, part in enumerate(chunks, 1):
        remaining = max_panels - len(panels)
        if remaining <= 0:
            break
        requested = min(6, max(1, max_panels // len(chunks)), remaining)
        instruction = (
            'You are Cydonia, a storyboard scriptwriter. Adapt the following Turkish story segment '
            'faithfully into EXACTLY '+str(requested)+' cinematic PHOTOREALISTIC film still panels. '
            'No cartoon, anime, illustration, comic lineart, fantasy exaggeration or text in image. '
            'Same characters must have consistent appearance across panels. '
            'Return ONLY valid JSON object: {"panels":[{"visual_prompt":"English photographic shot description '
            'including identity, pose, camera, setting, lighting, composition; NO text",'
            '"caption":"short Turkish narration", "dialogue":[{"speaker":"Turkish name",'
            '"text":"short Turkish sentence"}]}]}. '
            'Use exact events in source order. Do not invent major plot events or add explicit content. '
            'Maximum 2 dialogue bubbles per panel, each no more than 90 characters. '
            'Make visual prompt self-contained, 40-100 English words. '
            'Character description from preceding segment if any: '+character_context[:500]+'\n\n'
            'STORY PART '+str(ix)+'/'+str(len(chunks))+':\n'+part
        )
        task_id = bridge_submit({'task_type':'chat','model':'cydonia','prompt':instruction[:9000],
                                 'messages':[{'role':'user','content':instruction[:9000]}],
                                 'temperature':0.3})
        response = bridge_wait(task_id, CHAT_TIMEOUT)
        new = parse_json_output(response)[:remaining]
        panels.extend(new)
        character_context = ' | '.join(p['visual_prompt'][:90] for p in new[:3])
        progress(f'Planlama {ix}/{len(chunks)} · {len(panels)} panel')
    if not panels:
        raise ValueError('Hiç panel üretilemedi.')
    return panels


def font(size, bold=False):
    files = ['/usr/share/fonts/truetype/dejavu/DejaVuSans' + ('-Bold' if bold else '') + '.ttf',
             '/usr/share/fonts/truetype/liberation2/LiberationSans' + ('-Bold' if bold else '-Regular')+'.ttf']
    for f in files:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def wrap(draw, text, f, maxwidth):
    words = str(text).split()
    lines = []
    line = ''
    for word in words:
        candidate = (line+' '+word).strip()
        if draw.textbbox((0,0), candidate, font=f)[2] > maxwidth and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def render_pdf(panel_files, panels, target):
    """Mobile-readable 900x1600 pixel portrait PDF. Two cinematic panels per page."""
    page_w, page_h = 900, 1600
    pages = []
    title_font = font(26, True)
    line_font = font(21)
    who_font = font(20, True)
    small_font = font(18)
    for page_no, start in enumerate(range(0, len(panels), 2), 1):
        canvas = Image.new('RGB', (page_w, page_h), (245,245,245))
        draw = ImageDraw.Draw(canvas)
        draw.text((30,16), f'COMIC BOOK · SAYFA {page_no}', font=title_font, fill=(28,28,32))
        for slot, index in enumerate(range(start, min(start+2, len(panels)))):
            panel = panels[index]
            top = 66+slot*748
            draw.rounded_rectangle((18, top, 881, top+720), radius=14,
                                   fill='white', outline=(24,24,24), width=4)
            with Image.open(panel_files[index]) as pic:
                pic = ImageOps.fit(pic.convert('RGB'), (836, 515), method=Image.Resampling.LANCZOS)
                canvas.paste(pic, (32, top+14))
            text_y = top+538
            if panel.get('caption'):
                for line in wrap(draw, panel['caption'], small_font, 816)[:2]:
                    draw.text((42,text_y), line, font=small_font, fill=(55,55,55))
                    text_y += 24
            for bubble in panel.get('dialogue',[])[:2]:
                who = bubble.get('speaker', '')
                body = bubble.get('text', '')
                if not body:
                    continue
                phrase = (who+': ' if who else '')+body
                lines = wrap(draw, phrase, line_font, 795)[:2]
                height = len(lines)*27+12
                if text_y+height > top+708:
                    break
                draw.rounded_rectangle((40,text_y-3,859,text_y+height), radius=15,
                                       fill=(255,255,255), outline=(70,70,70), width=2)
                for row in lines:
                    draw.text((56,text_y+3), row, font=line_font, fill=(17,17,17))
                    text_y += 27
                text_y += 17
        pages.append(canvas)
    if not pages:
        raise RuntimeError('PDF oluşturulacak görsel yok.')
    target.parent.mkdir(parents=True,exist_ok=True)
    # Image-only PDF; texts have already been correctly rendered in Turkish via Pillow.
    pages[0].save(str(target), format='PDF', save_all=True, append_images=pages[1:],
                  resolution=110.0, quality=85)
    os.chmod(target, 0o600)
    return len(pages)


def telegram_complete():
    token = os.environ.get('COMIC_TELEGRAM_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('COMIC_TELEGRAM_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID')
    # Optional local .env; never log tokens.
    conf = BASE / 'comic_telegram.env'
    if conf.exists() and not (token and chat_id):
        env = {}
        for line in conf.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1)
                env[k.strip()] = v.strip().strip('"\'')
        token = token or env.get('COMIC_TELEGRAM_BOT_TOKEN')
        chat_id = chat_id or env.get('COMIC_TELEGRAM_CHAT_ID')
    if token and chat_id:
        r = requests.post('https://api.telegram.org/bot'+token+'/sendMessage',
                          json={'chat_id':chat_id,'text':'Görev tamamlandı.'},timeout=15)
        r.raise_for_status()
    else:
        LOG.warning('Telegram bildirim ayarı yok; tamamlanma bildirimi gönderilemedi.')


def register(app, app_globals):
    db = app_globals['db']
    set_job = app_globals['set_job']
    decrypt_string = app_globals['decrypt_string']
    migrate(db)
    bp = Blueprint('fiction_comics', __name__)

    def update(job_id, status=None, detail=None, panels=None, pages=None, error=None):
        with db() as conn:
            values = []
            clause = []
            if status is not None:
                clause.append('status=?'); values.append(status)
            if panels is not None:
                clause.append('panel_count=?'); values.append(panels)
            if pages is not None:
                clause.append('page_count=?'); values.append(pages)
            if error is not None:
                clause.append('error=?'); values.append(str(error)[:600])
            clause.append('updated_at=?'); values.append(utcnow())
            values.append(job_id)
            conn.execute('UPDATE comic_projects SET '+','.join(clause)+' WHERE id=?',values)
        extra = {'status': status, 'detail': detail}
        if error is not None:
            extra['error'] = str(error)[:600]
        set_job(job_id, **{k:v for k,v in extra.items() if v is not None})

    def work(job_id, source, source_kind):
        try:
            update(job_id,status='running',detail='Hikâye hazırlanıyor')
            if source_kind == 'links':
                def on_page(n, url):
                    update(job_id,detail=f'Bağlantı okunuyor · {n}. sayfa')
                story, urls = collect_links(source, max_pages=60, on_page=on_page)
            else:
                story = source
            if len(story.strip()) < 80:
                raise ValueError('Hikâye çok kısa.')
            if len(story) > MAX_SOURCE_CHARS:
                raise ValueError('Metin çok uzun.')
            # Find existing bridge image endpoint before committing GPU hours.
            bridge_image_route()
            update(job_id,detail='Cydonia hikâyeyi sahnelere ayırıyor')
            panels = plan_panels(story, lambda s:update(job_id,detail=s), max_panels=60)
            update(job_id,panels=len(panels),detail=f'{len(panels)} panel planlandı')
            work_dir = OUTPUT_ROOT / job_id
            work_dir.mkdir(mode=0o700,exist_ok=True)
            work_dir.chmod(0o700)
            panel_files = []
            for i,panel in enumerate(panels,1):
                path = work_dir / (f'{i:04d}.png')
                if not path.exists():
                    prompt = ('Photorealistic cinematic movie frame, authentic real human faces, '
                              'natural skin texture and expressions, detailed environment, '
                              'professional film photography, NOT artwork, NOT comic illustration. '
                              + panel['visual_prompt'])[:1750]
                    task_id = bridge_submit({'task_type':'scene','scene_count':1,
                                             'prompt':prompt[:9000], 'context':prompt[:4000],
                                             'width':768,'height':768,'steps':20,'cfg':5.5,'seed':-1})
                    bridge_wait(task_id,SCENE_TIMEOUT)
                    data = image_bytes(task_id,1)
                    path.write_bytes(data)
                    path.chmod(0o600)
                panel_files.append(path)
                update(job_id,detail=f'RealVisXL sahne {i}/{len(panels)}')
            target = work_dir / 'comic.pdf'
            count = render_pdf(panel_files, panels, target)
            # Intermediate images are removed when all pages are assembled; PDF is the output.
            for p in panel_files:
                p.unlink(missing_ok=True)
            update(job_id,status='complete',detail=f'PDF hazır · {count} sayfa',pages=count)
            try:
                telegram_complete()
            except Exception:
                LOG.exception('Telegram notification failed for comic %s', job_id)
        except Exception as exc:
            LOG.exception('Comic creation failed: %s', job_id)
            update(job_id,status='error', detail='Comic üretimi başarısız', error=str(exc))

    @bp.post('/api/comics')
    def create_comic():
        data = request.get_json(silent=True)
        if not isinstance(data,dict):
            return jsonify(error='JSON gerekiyor'),400
        raw = data.get('text','')
        if not isinstance(raw,str) or len(raw)>MAX_SOURCE_CHARS:
            return jsonify(error='Metin sınırı aşıldı'),400
        raw = raw.strip()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            active = conn.execute("SELECT id FROM jobs WHERE status NOT IN ('complete','error') LIMIT 1").fetchone()
            if active:
                return jsonify(error='Önce devam eden GPU işinin bitmesini bekleyin.',job_id=active['id']),409
            if not raw:
                row = conn.execute("SELECT content FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()
                if not row:
                    return jsonify(error='Önce hikâyeyi Cydonia ile oluşturun veya metin/URL girin.'),400
                raw = decrypt_string(row['content']).strip()
            if not raw:
                return jsonify(error='Hikâye boş'),400
            try:
                source_kind,source = parse_user_input(raw)
            except StoryFetchError as exc:
                return jsonify(error=str(exc)),400
            job_id = uuid.uuid4().hex[:24]
            timestamp = utcnow()
            conn.execute('INSERT INTO jobs(id,kind,status,detail,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                         (job_id,'comic','queued','Comic hazırlanıyor',timestamp,timestamp))
            conn.execute('INSERT INTO comic_projects(id,source_kind,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                         (job_id,source_kind,'queued',timestamp,timestamp))
        threading.Thread(target=work,args=(job_id,source,source_kind),daemon=True).start()
        return jsonify(job_id=job_id),202

    @bp.get('/api/comics')
    def list_comics():
        with db() as conn:
            rows=conn.execute('SELECT id,status,panel_count,page_count,error,created_at FROM comic_projects ORDER BY created_at DESC LIMIT 60').fetchall()
        items=[]
        for row in rows:
            d=dict(row)
            output=OUTPUT_ROOT/d['id']/'comic.pdf'
            if d['status']=='complete' and output.is_file():
                d['pdf_url']='/outputs/comics/'+d['id']+'.pdf'
            items.append(d)
        return jsonify(items=items)

    @bp.get('/outputs/comics/<job_id>.pdf')
    def get_comic(job_id):
        if not re.fullmatch('[0-9a-f]{24}',job_id):
            abort(404)
        with db() as conn:
            row=conn.execute('SELECT status FROM comic_projects WHERE id=?',(job_id,)).fetchone()
        if not row or row['status']!='complete':
            abort(404)
        target=OUTPUT_ROOT/job_id/'comic.pdf'
        if not target.is_file():
            abort(404)
        response=send_file(target,mimetype='application/pdf',as_attachment=False,
                           download_name=f'Comic-{job_id}.pdf', conditional=True)
        response.headers['Cache-Control']='no-store'
        return response

    app.register_blueprint(bp)