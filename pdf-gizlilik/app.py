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


ZOOM_LEVELS = (.5, .67, .8, 1, 1.25, 1.5, 2, 2.5, 3, 4)
FIT_ZOOM = ZOOM_LEVELS.index(1)
MAX_PREVIEW_PIXELS = 16_000_000


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PDF Gizlilik v5 | Çevrimdışı Anonimleştirme')
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
        self.zoom_index = FIT_ZOOM
        self.view_rotations = {}
        self.preview_bounds = None
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
        self.view_hint = ttk.Label(viewtop, text='Sürükle ve karart')
        self.view_hint.pack(side='right')
        viewtools = ttk.Frame(mid)
        viewtools.pack(fill='x', pady=(4, 0))
        ttk.Button(viewtools, text='Sola 90°', command=lambda: self.rotate_view(-90)).pack(side='left')
        ttk.Button(viewtools, text='Sağa 90°', command=lambda: self.rotate_view(90)).pack(side='left', padx=(3, 10))
        self.zoom_out_button = ttk.Button(viewtools, text='−', width=3, state='disabled',
                                          command=lambda: self.change_zoom(-1))
        self.zoom_out_button.pack(side='left')
        self.zoom_text = tk.StringVar(value='%100')
        ttk.Label(viewtools, textvariable=self.zoom_text, width=6, anchor='center').pack(side='left')
        self.zoom_in_button = ttk.Button(viewtools, text='+', width=3, state='disabled',
                                         command=lambda: self.change_zoom(1))
        self.zoom_in_button.pack(side='left')
        ttk.Button(viewtools, text='Sığdır', command=self.fit_view).pack(side='left', padx=5)

        viewport = ttk.Frame(mid)
        viewport.pack(fill='both', expand=True, pady=5)
        viewport.rowconfigure(0, weight=1)
        viewport.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(viewport, bg='#cbd5e1', highlightthickness=0, cursor='crosshair')
        self.canvas.grid(row=0, column=0, sticky='nsew')
        scroll_x = ttk.Scrollbar(viewport, orient='horizontal', command=self.canvas.xview)
        scroll_y = ttk.Scrollbar(viewport, orient='vertical', command=self.canvas.yview)
        scroll_x.grid(row=1, column=0, sticky='ew')
        scroll_y.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)
        self.canvas.bind('<Configure>', lambda _: self.show_page())
        self.canvas.bind('<MouseWheel>', self.scroll_view)
        self.canvas.bind('<Control-MouseWheel>', self.zoom_wheel)
        self.canvas.bind('<ButtonPress-1>', self._drag_start)
        self.canvas.bind('<B1-Motion>', self._drag_move)
        self.canvas.bind('<ButtonRelease-1>', self._drag_end)
        ttk.Button(mid, text='Bu sayfadaki elle çizilen karartmaları sil',
                   command=self.clear_manual).pack(fill='x')

        ttk.Label(right, text='Yüksek riskli sayfalar', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        ttk.Label(right, text='Ctrl ile ayrı sayfaları, Shift ile bir aralığı seçin. '
                              'Çift tıklayarak tek bir sayfanın durumunu değiştirin.',
                  wraplength=260).pack(anchor='w', pady=5)
        risk_list = ttk.Frame(right)
        risk_list.pack(fill='both', expand=True)
        self.risks = ttk.Treeview(risk_list, columns=('state', 'reason'), show='headings', selectmode='extended')
        self.risks.heading('state', text='Sayfa / durum')
        self.risks.heading('reason', text='Uyarı')
        self.risks.column('state', width=110, stretch=False)
        self.risks.column('reason', width=225, stretch=True)
        risk_scroll = ttk.Scrollbar(risk_list, orient='vertical', command=self.risks.yview)
        self.risks.configure(yscrollcommand=risk_scroll.set)
        risk_scroll.pack(side='right', fill='y')
        self.risks.pack(side='left', fill='both', expand=True)
        self.risks.bind('<Double-1>', self.toggle_risk)
        self.risks.bind('<<TreeviewSelect>>', self.select_risk)
        risk_actions = ttk.Frame(right)
        risk_actions.pack(fill='x', pady=(5, 0))
        self.approve_selected_button = ttk.Button(risk_actions, text='Seçilenleri Onayla', state='disabled',
                                                   command=self.approve_risks)
        self.approve_selected_button.pack(side='left', padx=(0, 4))
        self.approve_all_button = ttk.Button(risk_actions, text='Tümünü Onayla', state='disabled',
                                              command=lambda: self.approve_risks(all_pages=True))
        self.approve_all_button.pack(side='left')
        self.retry_button = ttk.Button(right, text='Dışlananları Yeniden Tara', state='disabled',
                                       command=self.retry_excluded_pages)
        self.retry_button.pack(fill='x', pady=(4, 0))
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
        self._refresh_export_button()
        self._refresh_risk_buttons()

    def _export_selection(self):
        findings = [f for f in self.findings
                    if not self.filters_stale and f.selected and f.page not in self.omit_pages]
        manual = {page: tuple(rects) for page, rects in self.manual.items()
                  if rects and page not in self.omit_pages}
        return findings, manual

    def _refresh_export_button(self):
        findings, manual = self._export_selection()
        enabled = bool(self.path and not self.busy and (findings or manual))
        self.export_button.config(state='normal' if enabled else 'disabled')

    def ocr_engine_changed(self, _event=None):
        self.filters_stale = True
        self._refresh_export_button()
        self._refresh_risk_buttons()
        self._draw_preview_marks()
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
        self._refresh_export_button()
        self._refresh_risk_buttons()
        self._draw_preview_marks()
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
        self._refresh_export_button()
        self._refresh_risk_buttons()
        self._draw_preview_marks()
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
        self.filters_stale = False
        self.warnings = []
        self.omit_pages.clear()
        self.mandatory_omit.clear()
        self.approved_pages.clear()
        self.manual.clear()
        self.zoom_index = FIT_ZOOM
        self.view_rotations.clear()
        self.preview_bounds = None
        self.tree.delete(*self.tree.get_children())
        self.risks.delete(*self.risks.get_children())
        self.warning_total.set('Henüz tarama yapılmadı')
        self._refresh_risk_buttons()
        self._refresh_export_button()
        self.status.set(f'{Path(filename).name} — {self.page_count} sayfa. Bir alan çizin veya taramayı başlatın.')
        self.goto(0)

    def goto(self, n):
        if self.path is None or not (0 <= n < self.page_count):
            return
        same_page = self.page_no == n
        self.page_no = n
        self.page_text.set(f'Sayfa {n + 1} / {self.page_count}')
        if not same_page or self.preview_bounds is None:
            self.show_page(keep_center=False)

    def rotate_view(self, degrees):
        if self.path:
            self.view_rotations[self.page_no] = (self.view_rotations.get(self.page_no, 0) + degrees) % 360
            self.show_page()

    def change_zoom(self, step):
        if step > 0 and self.zoom_in_button.instate(['disabled']):
            return
        index = max(0, min(self.zoom_index + step, len(ZOOM_LEVELS) - 1))
        if self.path and index != self.zoom_index:
            self.zoom_index = index
            self.show_page()

    def fit_view(self):
        self.zoom_index = FIT_ZOOM
        self.show_page(keep_center=False)

    def scroll_view(self, event):
        if event.delta:
            steps = max(1, abs(event.delta) // 120)
            self.canvas.yview_scroll(-steps if event.delta > 0 else steps, 'units')
        return 'break'

    def zoom_wheel(self, event):
        if event.delta:
            self.change_zoom(1 if event.delta > 0 else -1)
        return 'break'

    def show_page(self, keep_center=True):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        center = None
        if keep_center and self.preview_bounds is not None:
            canvas_center = fitz.Point(self.canvas.canvasx(width / 2), self.canvas.canvasy(height / 2))
            center = canvas_center * self.preview_to_pdf
        self.drag_start = None
        self.preview_bounds = None
        if not self.path or width < 40 or height < 40:
            return
        try:
            with fitz.open(self.path) as pdf:
                page = pdf[self.page_no]
                angle = self.view_rotations.get(self.page_no, 0)
                display = page.rect * fitz.Matrix(angle)
                fit_scale = min(1.7, (width - 16) / display.width, (height - 16) / display.height)
                max_scale = (MAX_PREVIEW_PIXELS / display.get_area()) ** .5
                scale = min(fit_scale * ZOOM_LEVELS[self.zoom_index], max_scale)
                render = fitz.Matrix(scale, scale).prerotate(angle)
                pix = page.get_pixmap(matrix=render, alpha=False)
                img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                offset_x = max(8, (width - pix.width) / 2)
                offset_y = max(8, (height - pix.height) / 2)
                # The rendered bitmap starts at pix.x/y, which may be negative after rotation.
                origin = fitz.Matrix(1, 0, 0, 1, offset_x - pix.x, offset_y - pix.y)
                pdf_to_preview = page.rotation_matrix * render * origin
                bounds = page.rect * page.derotation_matrix
            self.show_image = ImageTk.PhotoImage(img)
            self.pdf_to_preview = pdf_to_preview
            self.preview_to_pdf = ~pdf_to_preview
            self.preview_bounds = bounds
            content_width = max(width, img.width + 16)
            content_height = max(height, img.height + 16)
            self.canvas.delete('all')
            self.canvas.create_image(offset_x, offset_y, image=self.show_image, anchor='nw', tags='page-image')
            self.canvas.configure(scrollregion=(0, 0, content_width, content_height))
            if center is None:
                self.canvas.xview_moveto(0)
                self.canvas.yview_moveto(0)
            else:
                position = center * pdf_to_preview
                self.canvas.xview_moveto((position.x - width / 2) / content_width)
                self.canvas.yview_moveto((position.y - height / 2) / content_height)
            self.zoom_text.set(f'%{round(100 * scale / fit_scale)}')
            self.zoom_out_button.configure(state='normal' if self.zoom_index > 0 else 'disabled')
            can_zoom_in = self.zoom_index < len(ZOOM_LEVELS) - 1 and scale < max_scale
            self.zoom_in_button.configure(state='normal' if can_zoom_in else 'disabled')
            self._draw_preview_marks()
        except Exception as exc:
            self.preview_bounds = None
            self.canvas.delete('all')
            self.status.set('Önizleme hatası: ' + type(exc).__name__)

    def _draw_preview_marks(self):
        self.drag_start = None
        self.canvas.delete('preview-mark', 'dragbox')
        excluded = self.page_no in self.omit_pages
        self.view_hint.configure(text='Çıktıdan dışlanacak' if excluded else 'Sürükle ve karart',
                                 foreground='#b91c1c' if excluded else '#172533')
        if self.preview_bounds is None:
            return
        for finding in self.findings:
            if finding.page == self.page_no and finding.selected and not self.filters_stale:
                self._draw_pdf_rect(finding.rect, '#eab308', 2)
        for rect in self.manual[self.page_no]:
            self._draw_pdf_rect(rect, '#dc2626', 3)

    def _draw_pdf_rect(self, rect, color, width):
        area = fitz.Rect(rect) & self.preview_bounds
        if not area.is_empty:
            self.canvas.create_rectangle(*tuple(area * self.pdf_to_preview),
                                         outline=color, width=width, tags='preview-mark')

    def _drag_start(self, event):
        if self.preview_bounds is None or self.busy:
            return
        point = fitz.Point(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if (point * self.preview_to_pdf) in self.preview_bounds:
            self.drag_start = tuple(point)

    def _drag_move(self, event):
        if not self.drag_start:
            return
        self.canvas.delete('dragbox')
        x, y = self.drag_start
        self.canvas.create_rectangle(x, y, self.canvas.canvasx(event.x), self.canvas.canvasy(event.y), outline='#dc2626',
                                     width=2, dash=(4, 3), tags='dragbox')

    def _drag_end(self, event):
        if not self.drag_start or self.preview_bounds is None or self.busy:
            return
        a, b = self.drag_start
        self.drag_start = None
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if abs(a - x) < 6 or abs(b - y) < 6:
            self.canvas.delete('dragbox')
            return
        displayed = fitz.Rect(min(a, x), min(b, y), max(a, x), max(b, y))
        area = (displayed * self.preview_to_pdf) & self.preview_bounds
        if area.is_empty:
            self.canvas.delete('dragbox')
            return
        self.manual[self.page_no].append(tuple(area))
        self._refresh_export_button()
        self._draw_preview_marks()
        if self.page_no in self.omit_pages:
            self.status.set('Alan işaretlendi. Bu sayfa şu anda çıktıdan dışlanıyor.')
        else:
            self.status.set(f'Bu sayfada elle işaretlenen {len(self.manual[self.page_no])} karartma alanı var. '
                            'Kaydetmek için Temiz PDF Oluştur’a basın.')

    def clear_manual(self):
        if self.busy:
            return
        self.manual[self.page_no].clear()
        self._refresh_export_button()
        self._draw_preview_marks()
        self.status.set('Bu sayfanın elle karartma işaretleri kaldırıldı.')

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
        self._draw_preview_marks()
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

    def retry_excluded_pages(self):
        if self.busy or self.filters_stale or not self.path or not self.omit_pages:
            return
        if not ocr_available():
            messagebox.showwarning('OCR kullanılamıyor', 'Dışlanan sayfaları yeniden taramak için Tesseract gerekiyor.')
            return
        pages = tuple(sorted(self.omit_pages))
        path = self.path
        terms = tuple(term.strip() for term in self.terms.get().split(',') if term.strip())
        filter_data = self.filter_store.snapshot()
        self.cancel_event.clear()
        self.set_busy(True, cancellable=True)
        self.progress['value'] = 0
        self.status.set(f'{len(pages)} dışlanan sayfa yeniden taranıyor.')

        def run():
            try:
                findings, warnings = scan_pdf(path, terms, self._progress, use_ocr=True,
                                              filter_data=filter_data, page_numbers=pages, retry_ocr=True)
                if self.cancel_event.is_set():
                    raise OcrCancelled('İşlem iptal edildi.')
                self.events.put(('retry_done', pages, findings, warnings))
            except OcrCancelled as exc:
                self.events.put(('cancelled', str(exc)))
            except Exception as exc:
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
                    self.filters_stale = False
                    found, warnings, working_path, converted, exe, engine, source_stamp = data
                    if self.path != working_path:
                        self.manual.clear()
                        self.view_rotations.clear()
                        self.preview_bounds = None
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
                    self.set_busy(False)
                    self.status.set(f'{engine} taraması tamamlandı: {len(self.findings)} bulgu, {len(self.omit_pages)} riskli sayfa.')
                    if engine == 'FineReader':
                        self.fr_info.set('FineReader: OCR çıktısı otomatik alındı ve filtrelendi')
                    self.show_page()
                elif kind == 'retry_done':
                    pages, found, warnings = data
                    self._merge_retry_results(pages, found, warnings)
                    self.set_busy(False)
                    self.status.set(f'{len(pages)} sayfa yeniden tarandı. '
                                    f'{len(self.mandatory_omit)} sayfanın zorunlu dışlaması sürüyor.')
                    self._draw_preview_marks()
                elif kind == 'fr_test_done':
                    self.set_busy(False)
                    self.fr_info.set('FineReader: örnek OCR ve otomatik çıktı doğrulandı')
                    self.status.set(data[0])
                    if not self.closing:
                        messagebox.showinfo('Bağlantı başarılı', data[0])
                elif kind == 'cancelled':
                    self.set_busy(False)
                    self.status.set(data[0])
                elif kind == 'export_done':
                    self.set_busy(False)
                    self.status.set('Temiz PDF oluşturuldu: ' + data[0])
                    if not self.closing:
                        messagebox.showinfo('İşlem tamamlandı',
                                            'PDF kaydedildi. Gemini’ye yüklemeden önce dosyayı görsel olarak inceleyin.\n\n' + data[0])
                elif kind == 'error':
                    self.set_busy(False)
                    self.status.set('HATA: ' + data[0])
                    if not self.closing:
                        messagebox.showerror('İşlem başarısız', data[0])
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            self.close_app()
            return
        self.after(100, self._process_events)

    def _merge_retry_results(self, pages, found, warnings):
        known = {(f.page, f.category, f.value, f.rect) for f in self.findings}
        disabled = {(f.category, f.value.casefold().strip()) for f in self.findings if not f.selected}
        for finding in found:
            key = (finding.page, finding.category, finding.value, finding.rect)
            if key not in known:
                known.add(key)
                finding.selected = (finding.category, finding.value.casefold().strip()) not in disabled
                self.findings.append(finding)
        self.warnings = [warning for warning in self.warnings if warning.page not in pages] + warnings
        self._update_lists(preserve_approvals=True)

    def _update_lists(self, preserve_approvals=False):
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
        self.mandatory_omit = {w.page for w in self.warnings if 'OCR' in w.reason or 'uygun değil' in w.reason}
        self._risk_info = defaultdict(list)
        for w in self.warnings:
            self._risk_info[w.page].append(w.reason)
        if not preserve_approvals:
            self.approved_pages.clear()
        self.approved_pages.intersection_update(self._risk_info)
        self.approved_pages.difference_update(self.mandatory_omit)
        self.omit_pages = set(self._risk_info) - self.approved_pages
        for n in sorted(self._risk_info):
            title = 'ZORUNLU DIŞLA' if n in self.mandatory_omit else 'DIŞLA'
            if n in self.approved_pages:
                title = 'ONAYLI'
            self.risks.insert('', 'end', iid=f'risk{n}', values=(f'{n + 1} · {title}', ' / '.join(self._risk_info[n])[:115]))
        self._risk_summary()

    def _risk_summary(self):
        pending = len(self.omit_pages - self.mandatory_omit)
        self.warning_total.set(f'{len(self.omit_pages)} / {self.page_count} sayfa çıktıdan çıkarılacak. '
                               f'{pending} sayfa onay bekliyor, {len(self.mandatory_omit)} sayfa zorunlu dışlanıyor.')
        self._refresh_risk_buttons()

    def _selected_risk_pages(self):
        return {int(row[4:]) for row in self.risks.selection()}

    def _refresh_risk_buttons(self):
        available = self.omit_pages - self.mandatory_omit
        selected = available & self._selected_risk_pages()
        self.approve_selected_button.configure(state='normal' if selected and not self.busy else 'disabled')
        self.approve_all_button.configure(state='normal' if available and not self.busy else 'disabled')
        can_retry = self.omit_pages and not self.busy and not self.filters_stale
        self.retry_button.configure(state='normal' if can_retry else 'disabled')

    def approve_risks(self, all_pages=False):
        if self.busy:
            return
        candidates = self.omit_pages if all_pages else self.omit_pages & self._selected_risk_pages()
        pages = candidates - self.mandatory_omit
        if not pages:
            return
        message = (f'{len(pages)} riskli sayfa çıktı PDF’sine dahil edilecek. '
                   'Harita veya pafta görüntüsü konumu açığa çıkarabilir.')
        blocked = len(candidates & self.mandatory_omit)
        if blocked:
            message += f'\n{blocked} sayfa zorunlu dışlama nedeniyle çıktı dışında kalacak.'
        if messagebox.askyesno('Riskli sayfaları onayla', message + '\n\nBu sayfaları inceleyip onaylıyor musunuz?'):
            self._set_risks_approved(pages, True)

    def _set_risks_approved(self, pages, approved):
        if approved:
            self.approved_pages.update(pages)
            self.omit_pages.difference_update(pages)
        else:
            self.approved_pages.difference_update(pages)
            self.omit_pages.update(pages)
        title = 'ONAYLI' if approved else 'DIŞLA'
        for page in pages:
            self.risks.set(f'risk{page}', 'state', f'{page + 1} · {title}')
        self._risk_summary()
        self._refresh_export_button()
        self._draw_preview_marks()

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
        self._refresh_export_button()
        self._draw_preview_marks()

    def _select_group(self, _event):
        selected = self.tree.selection()
        if selected and selected[0] in self._item_keys:
            self.goto(self.groups[self._item_keys[selected[0]]][0].page)

    def select_risk(self, _event):
        self._refresh_risk_buttons()
        selected = self.risks.selection()
        if selected:
            focused = self.risks.focus()
            row = focused if focused in selected else selected[0]
            page = int(row[4:])
            if self.page_no != page:
                self.goto(page)

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
            self._set_risks_approved({n}, True)
        else:
            self._set_risks_approved({n}, False)

    def start_export(self):
        if self.busy or not self.path:
            return
        if len(self.omit_pages) == self.page_count:
            messagebox.showwarning('Sayfa yok', 'Bütün sayfalar dışlanıyor; çıktı oluşturulamaz.')
            return
        findings, manual = self._export_selection()
        if not findings and not manual:
            messagebox.showwarning('Karartma alanı seçin', 'Sayfada bir alan çizin veya tarama listesinden bulgu seçin.')
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
        manual_count = sum(len(rects) for rects in manual.values())
        if not messagebox.askyesno('Son kontrol',
                f'{len(findings)} otomatik bulgu ve {manual_count} elle işaretlenen alan karartılacak.\n'
                f'{len(self.omit_pages)} sayfa çıkarılacak.\n\nYeni PDF kaydedilsin mi?'):
            return
        self.set_busy(True)
        source, omitted = self.path, set(self.omit_pages)
        def run():
            try:
                path = export_pdf(source, output, findings, manual, omitted)
                self.events.put(('export_done', path))
            except Exception as exc:
                self.events.put(('error', type(exc).__name__ + ': ' + str(exc)))
        threading.Thread(target=run, daemon=True).start()


if __name__ == '__main__':
    Application().mainloop()
