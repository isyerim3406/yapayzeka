"""PDF Gizlilik - tamamen yerel PDF anonimleştirme masaüstü aracı."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz
from PIL import Image, ImageTk

from engine import CATEGORY_ORDER, export_pdf, ocr_available, scan_pdf
from filters import FilterStore
from filter_dialog import FilterDialog
from finereader import (OcrSettings, OcrCancelled, validate_executable,
                        convert_document, test_connection)


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PDF Gizlilik | Çevrimdışı Anonimleştirme')
        self.geometry('1320x830')
        self.minsize(1050, 650)
        self.configure(bg='#edf1f5')
        self.path = None
        self.original_path = None
        self.ocr_cache = None
        self.cache_exe = None
        self.cache_source_stamp = None
        self.ocr_settings = OcrSettings()
        self.cancel_event = threading.Event()
        self.closing = False
        self.findings = []
        self.warnings = []
        self.omit_pages = set()
        self.mandatory_omit = set()
        self.approved_pages = set()
        self.manual = defaultdict(list)
        self.groups = {}
        self.page_no = 0
        self.page_count = 0
        self.show_image = None
        self.scale = 1
        self.offset_x = 0
        self.offset_y = 0
        self.drag_start = None
        self.events = queue.Queue()
        self.filter_store = FilterStore()
        self.busy = False
        self.filters_stale = False
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.close_app)
        if self.ocr_settings.error:
            self.status.set('OCR ayarları okunamadı. OCR motorunu yeniden seçip kaydedin.')
        self.after(100, self._process_events)

    def _build(self):
        st = ttk.Style(self)
        st.theme_use('clam')
        st.configure('Treeview', rowheight=26, font=('Segoe UI', 9))
        st.configure('TButton', padding=7, font=('Segoe UI', 9))
        st.configure('TLabel', font=('Segoe UI', 9))
        top = ttk.Frame(self, padding=10)
        top.pack(fill='x')
        ttk.Label(top, text='PDF GİZLİLİK', font=('Segoe UI', 17, 'bold')).pack(side='left', padx=(0, 20))
        self.open_button = ttk.Button(top, text='PDF Aç', command=self.open_file)
        self.open_button.pack(side='left', padx=3)
        self.filter_button = ttk.Button(top, text='Filtre Yönetimi', command=self.manage_filters)
        self.filter_button.pack(side='left', padx=3)
        self.scan_button = ttk.Button(top, text='Tüm Sayfaları Tara', command=self.start_scan)
        self.scan_button.pack(side='left', padx=3)
        self.export_button = ttk.Button(top, text='Temiz PDF Oluştur', command=self.start_export, state='disabled')
        self.export_button.pack(side='left', padx=3)
        self.ocr_status = ttk.Label(top, text='OCR: hazır' if ocr_available() else 'OCR: yok — taranmış sayfalar dışlanır')
        self.ocr_status.pack(side='right')

        line = ttk.Frame(self, padding=(10, 0, 10, 7))
        line.pack(fill='x')
        ttk.Label(line, text='Ek gizlenecek ifadeler (virgülle ayır):').pack(side='left')
        self.terms = ttk.Entry(line, width=85)
        self.terms.pack(side='left', fill='x', expand=True, padx=(8, 0))
        ocr_line = ttk.Frame(self, padding=(10, 0, 10, 7))
        ocr_line.pack(fill='x')
        ttk.Label(ocr_line, text='OCR motoru:').pack(side='left')
        self.ocr_engine = tk.StringVar(value=self.ocr_settings.data['engine'])
        self.engine_combo = ttk.Combobox(ocr_line, textvariable=self.ocr_engine,
                                        values=('Tesseract', 'FineReader'), state='readonly', width=14)
        self.engine_combo.pack(side='left', padx=7)
        self.engine_combo.bind('<<ComboboxSelected>>', self.ocr_engine_changed)
        self.fr_button = ttk.Button(ocr_line, text='FineReader Konumu', command=self.choose_finereader)
        self.fr_button.pack(side='left', padx=3)
        self.fr_test_button = ttk.Button(ocr_line, text='Bağlantıyı Dene', command=self.test_finereader)
        self.fr_test_button.pack(side='left', padx=3)
        self.cancel_button = ttk.Button(ocr_line, text='İptal', command=self.cancel_operation, state='disabled')
        self.cancel_button.pack(side='left', padx=3)
        self.fr_info = tk.StringVar(value='FineReader: ' + ('konum kayıtlı; deneme yapılabilir'
                                    if self.ocr_settings.data['finereader_exe'] else 'konum seçilmedi'))
        ttk.Label(ocr_line, textvariable=self.fr_info).pack(side='left', padx=8)
        self.progress = ttk.Progressbar(self, mode='determinate')
        self.progress.pack(fill='x', padx=10, pady=(0, 4))
        self.status = tk.StringVar(value='PDF seçin. Dosyalar bilgisayarınızdan dışarı gönderilmez.')
        ttk.Label(self, textvariable=self.status).pack(fill='x', padx=10, pady=(0, 5))

        main = ttk.Panedwindow(self, orient='horizontal')
        main.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        left = ttk.Frame(main)
        mid = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(mid, weight=6)
        main.add(right, weight=3)

        ttk.Label(left, text='Toplu gizleme listesi (çift tıkla: seç/kaldır)',
                  font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(0, 5))
        self.tree = ttk.Treeview(left, columns=('state', 'category', 'amount'), show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='Bulunan bilgi')
        self.tree.heading('state', text='Seç')
        self.tree.heading('category', text='Tür')
        self.tree.heading('amount', text='Adet')
        self.tree.column('#0', width=200, stretch=True)
        self.tree.column('state', width=38, anchor='center', stretch=False)
        self.tree.column('category', width=88, stretch=False)
        self.tree.column('amount', width=40, anchor='e', stretch=False)
        sy = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sy.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sy.pack(side='left', fill='y')
        self.tree.bind('<Double-1>', self._toggle_group)
        self.tree.bind('<<TreeviewSelect>>', self._select_group)

        viewtop = ttk.Frame(mid)
        viewtop.pack(fill='x')
        ttk.Button(viewtop, text='◀', width=4, command=lambda: self.goto(self.page_no - 1)).pack(side='left')
        self.page_text = tk.StringVar(value='Sayfa - / -')
        ttk.Label(viewtop, textvariable=self.page_text, width=20, anchor='center').pack(side='left')
        ttk.Button(viewtop, text='▶', width=4, command=lambda: self.goto(self.page_no + 1)).pack(side='left')
        ttk.Label(viewtop, text='Sayfada sürükle = elle karartma alanı').pack(side='right')
        self.canvas = tk.Canvas(mid, bg='#cbd5e1', highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True, pady=5)
        self.canvas.bind('<Configure>', lambda _: self.show_page())
        self.canvas.bind('<ButtonPress-1>', self._drag_start)
        self.canvas.bind('<B1-Motion>', self._drag_move)
        self.canvas.bind('<ButtonRelease-1>', self._drag_end)
        ttk.Button(mid, text='Bu sayfadaki elle çizilen karartmaları sil',
                   command=self.clear_manual).pack(fill='x')

        ttk.Label(right, text='Yüksek riskli sayfalar', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        ttk.Label(right, text='Pafta/harita sayfaları otomatik olarak çıktıya alınmaz.\n'
                              'Kontrol edip çift tıklayarak onaylayabilirsiniz.',
                  wraplength=260).pack(anchor='w', pady=5)
        self.risks = ttk.Treeview(right, columns=('state', 'reason'), show='headings')
        self.risks.heading('state', text='Sayfa / durum')
        self.risks.heading('reason', text='Uyarı')
        self.risks.column('state', width=110, stretch=False)
        self.risks.column('reason', width=225, stretch=True)
        self.risks.pack(fill='both', expand=True)
        self.risks.bind('<Double-1>', self.toggle_risk)
        self.risks.bind('<<TreeviewSelect>>', self.select_risk)
        self.warning_total = tk.StringVar(value='Henüz tarama yapılmadı')
        ttk.Label(right, textvariable=self.warning_total, wraplength=290).pack(anchor='w', pady=6)
        ttk.Separator(right, orient='horizontal').pack(fill='x', pady=5)
        ttk.Label(right, text='Güvenlik sınırı:', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        ttk.Label(right, text='Konumu harita geometrisi, uydu görüntüsü veya topoğrafyadan tanımak mümkün olabilir.\n'
                             'Otomatik karartma tam anonimlik garantisi değildir.\n'
                             'OCR başarısız sayfalar çıktıdan zorunlu olarak çıkarılır.\n'
                             'Çıktıyı Gemini’ye göndermeden önce mutlaka gözden geçirin.',
                  wraplength=295, justify='left').pack(anchor='w', pady=7)

    def set_busy(self, busy, cancellable=False):
        self.busy = busy
        for button in (self.open_button, self.filter_button, self.scan_button,
                       self.fr_button, self.fr_test_button):
            button.config(state='disabled' if busy else 'normal')
        self.engine_combo.config(state='disabled' if busy else 'readonly')
        self.cancel_button.config(state='normal' if busy and cancellable else 'disabled')

    def ocr_engine_changed(self, _event=None):
        self.filters_stale = True
        self.export_button.config(state='disabled')
        try:
            self.ocr_settings.save(self.ocr_engine.get(), self.ocr_settings.data['finereader_exe'])
        except OSError as exc:
            messagebox.showerror('OCR ayarı kaydedilemedi', str(exc))
        self.status.set('OCR motoru değişti. Tüm Sayfaları Tara ile yeniden tarayın.')

    def choose_finereader(self):
        if self.busy:
            return
        filename = filedialog.askopenfilename(title='Çalışan tam FineReader klasöründeki FineReaderOCR.exe dosyasını seçin',
                        initialfile='FineReaderOCR.exe', filetypes=[('FineReader OCR', '*.exe')])
        if not filename:
            return
        try:
            exe = validate_executable(filename)
            self.ocr_settings.save('FineReader', str(exe))
        except (ValueError, OSError) as exc:
            messagebox.showerror('FineReader konumu', str(exc))
            return
        self.ocr_engine.set('FineReader')
        self.filters_stale = True
        self.export_button.config(state='disabled')
        self.fr_info.set('FineReader: konum kayıtlı; Bağlantıyı Dene ile kontrol edin')
        self.status.set('FineReader konumu kaydedildi. Bağlantıyı Dene veya Tüm Sayfaları Tara düğmesini kullanın.')

    def _progress(self, n, total, text):
        if self.cancel_event.is_set():
            raise OcrCancelled('İşlem iptal edildi.')
        self.events.put(('progress', n, total, text))

    def test_finereader(self):
        if self.busy:
            return
        exe = self.ocr_settings.data['finereader_exe']
        try:
            validate_executable(exe)
        except (ValueError, OSError) as exc:
            messagebox.showinfo('FineReader seçin', str(exc))
            return
        self.cancel_event.clear()
        self.set_busy(True, cancellable=True)
        self.export_button.config(state='disabled')
        self.status.set('FineReader bağlantısı görüntülü örnek bir sayfayla deneniyor...')
        def run():
            try:
                result = test_connection(exe, cancel=self.cancel_event, progress=self._progress)
                self.events.put(('fr_test_done', result))
            except OcrCancelled as exc:
                self.events.put(('cancelled', str(exc)))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def cancel_operation(self):
        self.cancel_event.set()
        self.cancel_button.config(state='disabled')
        self.status.set('İşlemin durması bekleniyor...')

    def close_app(self):
        if self.busy:
            self.closing = True
            self.cancel_operation()
            return
        if self.ocr_cache:
            self.ocr_cache.cleanup()
        self.destroy()

    def manage_filters(self):
        if not self.busy:
            FilterDialog(self, self.filter_store, self.filters_saved)

    def filters_saved(self):
        self.filters_stale = True
        self.export_button.config(state='disabled')
        self.status.set('Filtreler kaydedildi. Açık PDF için Tüm Sayfaları Tara düğmesine basın.')

    def open_file(self):
        if self.busy:
            return
        filename = filedialog.askopenfilename(title='PDF seçin', filetypes=[('PDF dosyaları', '*.pdf')])
        if not filename:
            return
        try:
            with fitz.open(filename) as doc:
                if doc.needs_pass:
                    raise ValueError('Parolalı PDF desteklenmiyor.')
                self.page_count = len(doc)
        except Exception as exc:
            messagebox.showerror('PDF açılamadı', str(exc))
            return
        self.path = filename
        self.original_path = filename
        if self.ocr_cache:
            self.ocr_cache.cleanup()
            self.ocr_cache = None
        self.cache_exe = None
        self.cache_source_stamp = None
        self.findings = []
        self.warnings = []
        self.omit_pages.clear()
        self.mandatory_omit.clear()
        self.approved_pages.clear()
        self.manual.clear()
        self.tree.delete(*self.tree.get_children())
        self.risks.delete(*self.risks.get_children())
        self.export_button.config(state='disabled')
        self.status.set(f'{Path(filename).name} — {self.page_count} sayfa. Taramayı başlatın.')
        self.goto(0)

    def goto(self, n):
        if self.path is None or not (0 <= n < self.page_count):
            return
        self.page_no = n
        self.page_text.set(f'Sayfa {n + 1} / {self.page_count}')
        self.show_page()

    def show_page(self):
        if not self.path or self.canvas.winfo_width() < 40:
            return
        try:
            with fitz.open(self.path) as pdf:
                p = pdf[self.page_no]
                w = max(1, self.canvas.winfo_width() - 16)
                h = max(1, self.canvas.winfo_height() - 16)
                self.scale = min(1.7, w / p.rect.width, h / p.rect.height)
                pix = p.get_pixmap(matrix=fitz.Matrix(self.scale, self.scale), alpha=False)
                img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            self.show_image = ImageTk.PhotoImage(img)
            self.offset_x = (self.canvas.winfo_width() - img.width) / 2
            self.offset_y = (self.canvas.winfo_height() - img.height) / 2
            self.canvas.delete('all')
            self.canvas.create_image(self.offset_x, self.offset_y, image=self.show_image, anchor='nw')
            for f in self.findings:
                if f.page == self.page_no and f.selected:
                    self._draw_pdf_rect(f.rect, '#eab308', 2)
            for rect in self.manual[self.page_no]:
                self._draw_pdf_rect(rect, '#dc2626', 3)
            if self.page_no in self.omit_pages:
                self.canvas.create_text(12, 12, anchor='nw', fill='#ef4444', font=('Segoe UI', 14, 'bold'),
                                        text='BU SAYFA ÇIKTIDAN ÇIKARILACAK')
        except Exception as exc:
            self.status.set('Önizleme hatası: ' + type(exc).__name__)

    def _draw_pdf_rect(self, rect, color, width):
        x0, y0, x1, y1 = rect
        self.canvas.create_rectangle(self.offset_x + x0 * self.scale,
                                     self.offset_y + y0 * self.scale,
                                     self.offset_x + x1 * self.scale,
                                     self.offset_y + y1 * self.scale,
                                     outline=color, width=width)

    def _drag_start(self, event):
        if self.path is None or self.busy:
            return
        self.drag_start = (event.x, event.y)

    def _drag_move(self, event):
        if not self.drag_start:
            return
        self.canvas.delete('dragbox')
        x, y = self.drag_start
        self.canvas.create_rectangle(x, y, event.x, event.y, outline='#dc2626',
                                     width=2, dash=(4, 3), tags='dragbox')

    def _drag_end(self, event):
        if not self.drag_start or not self.path:
            return
        a, b = self.drag_start
        self.drag_start = None
        if abs(a - event.x) < 6 or abs(b - event.y) < 6:
            self.canvas.delete('dragbox')
            return
        xy = [(v - off) / self.scale for v, off in (
            (a, self.offset_x), (b, self.offset_y), (event.x, self.offset_x), (event.y, self.offset_y))]
        x0, x1 = sorted((xy[0], xy[2]))
        y0, y1 = sorted((xy[1], xy[3]))
        self.manual[self.page_no].append((x0, y0, x1, y1))
        self.show_page()

    def clear_manual(self):
        if self.busy:
            return
        self.manual[self.page_no].clear()
        self.show_page()

    def start_scan(self):
        if self.busy:
            return
        if self.filter_store.error:
            messagebox.showerror("Filtre dosyası okunamadı", "Kayıtlı filtreler okunamadı: " + self.filter_store.error + "\nFiltre Yönetimi ekranından kontrol edip kaydedin.")
            return
        if not self.path:
            messagebox.showinfo('PDF seçilmedi', 'Önce PDF açın.')
            return
        terms = tuple(t.strip() for t in self.terms.get().split(',') if t.strip())
        filter_data = self.filter_store.snapshot()
        engine = self.ocr_engine.get()
        exe = self.ocr_settings.data['finereader_exe']
        if engine == 'FineReader':
            try:
                validate_executable(exe)
            except (ValueError, OSError) as exc:
                messagebox.showinfo('FineReader konumu gerekli', str(exc))
                return
        try:
            stat = Path(self.original_path).stat()
            source_stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError as exc:
            messagebox.showerror('Kaynak PDF okunamadı', str(exc))
            return
        cache = self.ocr_cache if (engine == 'FineReader' and self.cache_exe == exe
                    and self.cache_source_stamp == source_stamp) else None
        next_path = str(cache.path) if cache else self.original_path
        path_may_change = (engine == 'FineReader' and cache is None) or next_path != self.path
        if any(self.manual.values()) and path_may_change:
            if not messagebox.askyesno('OCR çalışma sayfaları',
                    'OCR motoru yeni bir çalışma PDF’si kullanacak. Elle çizdiğiniz karartmaları yeni sayfalar üzerinde '
                    'tekrar çizmeniz gerekecek. Yeniden tarama başlasın mı?'):
                return
        self.cancel_event.clear()
        self.filters_stale = True
        self.set_busy(True, cancellable=True)
        self.scan_button.config(state='disabled')
        self.export_button.config(state='disabled')
        self.progress['value'] = 0
        path = self.original_path
        def run():
            converted = None
            try:
                working = cache
                if engine == 'FineReader' and working is None:
                    converted = convert_document(exe, path, cancel=self.cancel_event, progress=self._progress)
                    working = converted
                after = Path(path).stat()
                if (after.st_size, after.st_mtime_ns) != source_stamp:
                    raise RuntimeError('Kaynak PDF işlem sırasında değişti. Dosyayı yeniden açıp tarayın.')
                working_path = str(working.path) if working else path
                found, warnings = scan_pdf(working_path, terms, self._progress,
                    use_ocr=engine == 'Tesseract', filter_data=filter_data, external_ocr=engine == 'FineReader')
                if self.cancel_event.is_set():
                    raise OcrCancelled('İşlem iptal edildi.')
                self.events.put(('scan_done', found, warnings, working_path, converted, exe, engine, source_stamp))
            except OcrCancelled as exc:
                if converted:
                    converted.cleanup()
                self.events.put(('cancelled', str(exc)))
            except Exception as exc:
                if converted:
                    converted.cleanup()
                self.events.put(('error', type(exc).__name__ + ': ' + str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _process_events(self):
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == 'progress':
                    n, total, msg = data
                    self.progress['maximum'] = max(1, total)
                    self.progress['value'] = n
                    self.status.set(f'{msg}: {n} / {total}')
                elif kind == 'scan_done':
                    self.set_busy(False)
                    self.filters_stale = False
                    found, warnings, working_path, converted, exe, engine, source_stamp = data
                    if self.path != working_path:
                        self.manual.clear()
                    self.path = working_path
                    if converted:
                        if self.ocr_cache:
                            self.ocr_cache.cleanup()
                        self.ocr_cache = converted
                        self.cache_exe = exe
                        self.cache_source_stamp = source_stamp
                    with fitz.open(self.path) as doc:
                        self.page_count = len(doc)
                    self.page_no = min(self.page_no, self.page_count - 1)
                    self.page_text.set(f'Sayfa {self.page_no + 1} / {self.page_count}')
                    self.findings, self.warnings = found, warnings
                    self._update_lists()
                    self.scan_button.config(state='normal')
                    self.export_button.config(state='normal')
                    self.status.set(f'{engine} taraması tamamlandı: {len(self.findings)} bulgu, {len(self.omit_pages)} riskli sayfa.')
                    if engine == 'FineReader':
                        self.fr_info.set('FineReader: OCR çıktısı otomatik alındı ve filtrelendi')
                    self.show_page()
                elif kind == 'fr_test_done':
                    self.set_busy(False)
                    self.fr_info.set('FineReader: örnek OCR ve otomatik çıktı doğrulandı')
                    self.status.set(data[0])
                    if self.findings and not self.filters_stale:
                        self.export_button.config(state='normal')
                    if not self.closing:
                        messagebox.showinfo('Bağlantı başarılı', data[0])
                elif kind == 'cancelled':
                    self.set_busy(False)
                    self.status.set(data[0])
                    if self.findings and not self.filters_stale:
                        self.export_button.config(state='normal')
                elif kind == 'export_done':
                    self.set_busy(False)
                    self.scan_button.config(state='normal')
                    self.export_button.config(state='normal')
                    self.status.set('Temiz PDF oluşturuldu: ' + data[0])
                    if not self.closing:
                        messagebox.showinfo('İşlem tamamlandı',
                                            'PDF kaydedildi. Gemini’ye yüklemeden önce dosyayı görsel olarak inceleyin.\n\n' + data[0])
                elif kind == 'error':
                    self.set_busy(False)
                    self.scan_button.config(state='normal')
                    if self.findings and not self.filters_stale:
                        self.export_button.config(state='normal')
                    self.status.set('HATA: ' + data[0])
                    if not self.closing:
                        messagebox.showerror('İşlem başarısız', data[0])
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            self.close_app()
            return
        self.after(100, self._process_events)

    def _update_lists(self):
        self.groups.clear()
        self.tree.delete(*self.tree.get_children())
        for f in self.findings:
            key = (f.category, f.value.casefold().strip())
            self.groups.setdefault(key, []).append(f)
        for cat in (*CATEGORY_ORDER, *sorted({f.category for f in self.findings} - set(CATEGORY_ORDER))):
            cat_keys = [k for k in self.groups if k[0] == cat]
            if not cat_keys:
                continue
            root = self.tree.insert('', 'end', text=cat, values=('', '', sum(len(self.groups[k]) for k in cat_keys)), open=True)
            for key in sorted(cat_keys, key=lambda k: -len(self.groups[k])):
                values = self.groups[key]
                mark = '☑' if all(f.selected for f in values) else '☐'
                self.tree.insert(root, 'end', text=values[0].value[:70], values=(mark, cat, len(values)),
                                 tags=('finding',))
        self._item_keys = {}
        for root in self.tree.get_children():
            cat = self.tree.item(root, 'text')
            for item in self.tree.get_children(root):
                val = self.tree.item(item, 'text')
                keys = [k for k, fs in self.groups.items() if k[0] == cat and fs[0].value[:70] == val]
                if keys:
                    self._item_keys[item] = keys[0]
        self.risks.delete(*self.risks.get_children())
        self.omit_pages = {w.page for w in self.warnings}
        self.mandatory_omit = {w.page for w in self.warnings if 'OCR' in w.reason or 'uygun değil' in w.reason}
        self._risk_info = defaultdict(list)
        for w in self.warnings:
            self._risk_info[w.page].append(w.reason)
        for n in sorted(self._risk_info):
            title = 'ZORUNLU DIŞLA' if n in self.mandatory_omit else 'DIŞLA'
            self.risks.insert('', 'end', iid=f'risk{n}', values=(f'{n + 1} · {title}', ' / '.join(self._risk_info[n])[:115]))
        self._risk_summary()

    def _risk_summary(self):
        self.warning_total.set(f'Çıkarılacak sayfa: {len(self.omit_pages)} / {self.page_count}. '
                               'Riskli sayfayı açıp inceleyin; çift tık onaylar. '
                               'OCR başarısız sayfa onaylanamaz.')

    def _toggle_group(self, event):
        if self.busy:
            return
        item = self.tree.identify_row(event.y)
        key = self._item_keys.get(item)
        if not key:
            return
        findings = self.groups[key]
        new_value = not all(f.selected for f in findings)
        for f in findings:
            f.selected = new_value
        self.tree.set(item, 'state', '☑' if new_value else '☐')
        self.show_page()

    def _select_group(self, _event):
        selected = self.tree.selection()
        if selected and selected[0] in self._item_keys:
            self.goto(self.groups[self._item_keys[selected[0]]][0].page)

    def select_risk(self, _event):
        selected = self.risks.selection()
        if selected:
            self.goto(int(selected[0].replace('risk', '')))

    def toggle_risk(self, event):
        if self.busy:
            return
        row = self.risks.identify_row(event.y)
        if not row.startswith('risk'):
            return
        n = int(row[4:])
        if n in self.mandatory_omit:
            messagebox.showwarning('Kapatılamayan risk', 'Bu sayfada OCR tamamlanamadı. Çıktıdan çıkarılması zorunlu.')
            return
        if n in self.omit_pages:
            if not messagebox.askyesno('Konum gizliliği uyarısı',
                    f'Sayfa {n+1} harita, koordinat ızgarası veya uydu görünümünden konumu açığa çıkarabilir. '
                    'Bu sayfayı gözle kontrol edip Gemini’ye gönderilmesini kabul ediyor musunuz?'):
                return
            self.omit_pages.remove(n)
            self.risks.set(row, 'state', f'{n + 1} · ONAYLI')
        else:
            self.omit_pages.add(n)
            self.risks.set(row, 'state', f'{n + 1} · DIŞLA')
        self._risk_summary()
        self.show_page()

    def start_export(self):
        if self.busy or self.filters_stale:
            return
        if not self.path or not self.findings:
            messagebox.showwarning('Bulgular yok', 'Önce tarama yapın ve en az bir bulguyu işaretleyin.')
            return
        selected = sum(f.selected for f in self.findings)
        if not selected and not any(self.manual.values()):
            messagebox.showwarning('Karartma yok', 'Hiçbir alan seçilmedi.')
            return
        if len(self.omit_pages) == self.page_count:
            messagebox.showwarning('Sayfa yok', 'Bütün sayfalar dışlanıyor; çıktı oluşturulamaz.')
            return
        output = filedialog.asksaveasfilename(
            title='Yeni anonim PDF', defaultextension='.pdf',
            initialfile=Path(self.original_path or self.path).stem + '_ANONIM.pdf',
            filetypes=[('PDF', '*.pdf')])
        if not output:
            return
        if Path(output).exists():
            messagebox.showerror('Üzerine yazma engellendi', 'Çıktı zaten var. Yeni ad seçin.')
            return
        if not messagebox.askyesno('Son kontrol',
                f'{selected} işaretli eşleşme karartılacak, {len(self.omit_pages)} sayfa çıkarılacak.\n'
                'Orijinal PDF korunacak; yedek oluşturulmayacak.\n\nDevam edilsin mi?'):
            return
        self.export_button.config(state='disabled')
        self.scan_button.config(state='disabled')
        self.set_busy(True)
        source, findings, manual, omitted = self.path, list(self.findings), dict(self.manual), set(self.omit_pages)
        def run():
            try:
                path = export_pdf(source, output, findings, manual, omitted)
                self.events.put(('export_done', path))
            except Exception as exc:
                self.events.put(('error', type(exc).__name__ + ': ' + str(exc)))
        threading.Thread(target=run, daemon=True).start()


if __name__ == '__main__':
    Application().mainloop()
