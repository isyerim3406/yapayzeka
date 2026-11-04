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


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PDF Gizlilik | Çevrimdışı Anonimleştirme')
        self.geometry('1320x830')
        self.minsize(1050, 650)
        self.configure(bg='#edf1f5')
        self.path = None
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
        self._build()
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
        ttk.Button(top, text='PDF Aç', command=self.open_file).pack(side='left', padx=3)
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

    def open_file(self):
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
        if self.path is None:
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
        self.manual[self.page_no].clear()
        self.show_page()

    def start_scan(self):
        if not self.path:
            messagebox.showinfo('PDF seçilmedi', 'Önce PDF açın.')
            return
        terms = tuple(t.strip() for t in self.terms.get().split(',') if t.strip())
        self.scan_button.config(state='disabled')
        self.export_button.config(state='disabled')
        self.progress['value'] = 0
        path = self.path
        def run():
            try:
                found, warnings = scan_pdf(path, terms, lambda n, total, msg:
                    self.events.put(('progress', n, total, msg)), use_ocr=True)
                self.events.put(('scan_done', found, warnings))
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
                    self.findings, self.warnings = data
                    self._update_lists()
                    self.scan_button.config(state='normal')
                    self.export_button.config(state='normal')
                    self.status.set(f'Tarama tamamlandı: {len(self.findings)} bulgu, {len(self.omit_pages)} riskli sayfa.')
                    self.show_page()
                elif kind == 'export_done':
                    self.scan_button.config(state='normal')
                    self.export_button.config(state='normal')
                    self.status.set('Temiz PDF oluşturuldu: ' + data[0])
                    messagebox.showinfo('İşlem tamamlandı',
                                        'PDF kaydedildi. Gemini’ye yüklemeden önce dosyayı görsel olarak inceleyin.\n\n' + data[0])
                elif kind == 'error':
                    self.scan_button.config(state='normal')
                    if self.findings:
                        self.export_button.config(state='normal')
                    self.status.set('HATA: ' + data[0])
                    messagebox.showerror('İşlem başarısız', data[0])
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def _update_lists(self):
        self.groups.clear()
        self.tree.delete(*self.tree.get_children())
        for f in self.findings:
            key = (f.category, f.value.casefold().strip())
            self.groups.setdefault(key, []).append(f)
        for cat in CATEGORY_ORDER:
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
            initialfile=Path(self.path).stem + '_ANONIM.pdf',
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
