"""Native filter management; all edits remain local."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox
from filters import LINE_PREFIX, next_user_rule_id, normalize, validate


class FilterDialog(tk.Toplevel):
    def __init__(self, parent, store, on_saved):
        super().__init__(parent)
        self.store=store;self.data=store.snapshot();self.on_saved=on_saved
        self.selected=None;self.dirty=False;self.loading=False
        self.title('Filtre Yönetimi');self.geometry('960x680');self.minsize(760,580)
        self.transient(parent);self.grab_set()
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.search=tk.StringVar();self.name=tk.StringVar();self.category=tk.StringVar(value='Kullanıcı')
        self.kind=tk.StringVar(value='Alan başlığı');self.enabled=tk.BooleanVar(value=True)
        self.multiline=tk.BooleanVar(value=False);self.lines=tk.IntVar(value=2)
        top=ttk.Frame(self,padding=12);top.pack(fill='x')
        ttk.Label(top,text='Kalıcı filtreler',font=('Segoe UI',15,'bold')).pack(anchor='w')
        ttk.Label(top,text='Alan başlığı değeri, satır başı filtresi etiketle başlayan satırı, birebir ifade yazdığınız metni bulur.',
                  wraplength=900).pack(anchor='w')
        body=ttk.Panedwindow(self,orient='horizontal');body.pack(fill='both',expand=True,padx=12)
        left=ttk.Frame(body);right=ttk.Frame(body,padding=(15,0,0,0));body.add(left,weight=1);body.add(right,weight=2)
        ttk.Entry(left,textvariable=self.search).pack(fill='x',pady=(0,8))
        self.tree=ttk.Treeview(left,columns=('on',),show='tree headings',selectmode='browse')
        self.tree.heading('#0',text='Filtre adı');self.tree.heading('on',text='Etkin');self.tree.column('on',width=45,stretch=False)
        sb=ttk.Scrollbar(left,command=self.tree.yview);self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.select)
        self.search.trace_add('write',lambda *_:self.refresh())
        for label,var in [('Filtre adı',self.name),('Kategori',self.category)]:
            ttk.Label(right,text=label).pack(anchor='w');ttk.Entry(right,textvariable=var).pack(fill='x',pady=(0,7))
        ttk.Label(right,text='Tür').pack(anchor='w')
        self.typebox=ttk.Combobox(right,textvariable=self.kind,values=('Alan başlığı','Satır başı','Birebir ifade'),state='readonly')
        self.typebox.pack(fill='x',pady=(0,7))
        self.typebox.bind('<<ComboboxSelected>>', self.update_line_options)
        ttk.Label(right,text='Başlıklar / ifadeler (her satıra bir tane)').pack(anchor='w')
        self.aliases=tk.Text(right,height=7,wrap='word');self.aliases.pack(fill='both',expand=True)
        ttk.Checkbutton(right,text='Etkin',variable=self.enabled).pack(anchor='w',pady=4)
        self.multiline_box=ttk.Checkbutton(right,text='Alt satıra devam eden değeri de ara',variable=self.multiline)
        self.multiline_box.pack(anchor='w')
        row=ttk.Frame(right);row.pack(fill='x',pady=4);ttk.Label(row,text='En fazla satır:').pack(side='left')
        self.line_count=ttk.Spinbox(row,from_=1,to=10,textvariable=self.lines,width=5)
        self.line_count.pack(side='left',padx=8)
        ttk.Label(right,text='Örnek: Vergi No, VKN, Vergi Kimlik Numarası başlıklarını ayrı satırlara yazın.\nGörsel kaşe, imza ve filigranlar ayrıca kontrol edilmelidir.',wraplength=480).pack(anchor='w',pady=6)
        self.info=tk.StringVar(value='Değişiklikler Kaydet düğmesiyle kalıcı olur.')
        ttk.Label(self,textvariable=self.info,wraplength=900).pack(fill='x',padx=12,pady=6)
        bar=ttk.Frame(self,padding=12);bar.pack(fill='x')
        ttk.Button(bar,text='Yeni filtre',command=self.new).pack(side='left')
        ttk.Button(bar,text='Seçiliyi sil',command=self.delete).pack(side='left',padx=5)
        ttk.Button(bar,text='Kaydet',command=self.save).pack(side='right')
        ttk.Button(bar,text='Kapat',command=self.close).pack(side='right',padx=5)
        self.refresh()
        self.new()

    def rules(self):return self.data['rules']+self.data['user_rules']

    def current(self):return next((r for r in self.rules() if r['id']==self.selected),None)

    def update_line_options(self, _event=None):
        state = 'disabled' if self.kind.get() == 'Satır başı' else 'normal'
        self.multiline_box.configure(state=state)
        self.line_count.configure(state=state)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        q=normalize(self.search.get())
        for r in self.rules():
            if q in normalize(r['name']+' '+r['category']+' '.join(r['aliases'])):
                self.tree.insert('','end',iid=r['id'],text=r['name'],values=('✓' if r['enabled'] else '—',))

    def draft(self):
        r=copy.deepcopy(self.current()) or dict(id='',review_required=True,stop_at_next_label=True,notes='Kullanıcı filtresi',pattern='')
        r.update(name=self.name.get().strip(),category=self.category.get().strip(),enabled=self.enabled.get())
        if r.get('kind')!='regex':
            aliases = [a.strip() for a in self.aliases.get('1.0','end').splitlines() if a.strip()]
            if self.kind.get() == 'Satır başı':
                r.update(kind='field', aliases=aliases, scope=LINE_PREFIX, multiline=False, max_lines=1)
            else:
                kind = 'field' if self.kind.get() == 'Alan başlığı' else 'literal'
                scope = 'same_cell_or_labeled_value' if kind == 'field' else 'matched_text'
                r.update(kind=kind, aliases=aliases, scope=scope,
                         multiline=self.multiline.get(), max_lines=int(self.lines.get()))
        return r

    def changed(self):
        try:
            r=self.draft()
            return (r!=self.current()) if self.selected else bool(r['name'] or r['aliases'])
        except (ValueError,tk.TclError):return True

    def apply(self):
        if not self.changed():return True
        try:
            r=self.draft()
            if not r['id']:
                r['id']=next_user_rule_id(self.data)
            trial=copy.deepcopy(self.data)
            group='rules' if any(x['id']==r['id'] for x in trial['rules']) else 'user_rules'
            trial[group]=[x for x in trial[group] if x['id']!=r['id']]+[r]
            validate(trial)
        except (ValueError,tk.TclError) as exc:
            messagebox.showerror('Filtre kaydedilemedi',str(exc),parent=self);return False
        self.data=trial;self.selected=r['id'];self.dirty=True
        return True

    def select(self,_event=None):
        if self.loading:return
        ids=self.tree.selection()
        if not ids or ids[0]==self.selected:return
        target=ids[0]
        if not self.apply():return
        r=next(x for x in self.rules() if x['id']==target)
        self.selected=target;self.name.set(r['name']);self.category.set(r['category']);self.enabled.set(r['enabled'])
        self.multiline.set(r.get('multiline',False));self.lines.set(r.get('max_lines',2))
        kind = 'Satır başı' if r.get('scope') == LINE_PREFIX else {'field':'Alan başlığı','literal':'Birebir ifade','regex':'Hazır desen'}[r['kind']]
        self.kind.set(kind)
        self.update_line_options()
        self.typebox.configure(state='disabled' if r['kind']=='regex' else 'readonly')
        self.aliases.configure(state='normal');self.aliases.delete('1.0','end');self.aliases.insert('1.0','\n'.join(r['aliases']))
        if r['kind']=='regex':self.aliases.configure(state='disabled')
        self.info.set('Hazır desenin adını ve etkin durumunu değiştirebilirsiniz.' if r['kind']=='regex' else 'Değişiklikler Kaydet ile kalıcı olur.')

    def new(self):
        if not self.apply():return
        self.selected=None;self.name.set('');self.category.set('Kullanıcı');self.kind.set('Alan başlığı')
        self.enabled.set(True);self.multiline.set(False);self.lines.set(2)
        self.update_line_options()
        self.typebox.configure(state='readonly');self.aliases.configure(state='normal');self.aliases.delete('1.0','end')
        self.loading=True;self.tree.selection_remove(*self.tree.selection());self.loading=False

    def delete(self):
        if not self.selected:return
        if not messagebox.askyesno('Filtreyi sil','Seçili filtre silinsin mi?',parent=self):return
        for key in ('rules','user_rules'):self.data[key]=[r for r in self.data[key] if r['id']!=self.selected]
        self.selected=None;self.name.set('');self.aliases.configure(state='normal');self.aliases.delete('1.0','end')
        self.dirty=True;self.refresh();self.new()

    def save(self):
        if not self.apply():return
        try:self.store.save(self.data)
        except (ValueError,OSError) as exc:
            messagebox.showerror('Kaydedilemedi',str(exc),parent=self);return
        self.dirty=False;self.on_saved();self.refresh();self.info.set(f'{len(self.rules())} filtre kaydedildi. Sonraki taramada kullanılacak.')

    def close(self):
        if self.dirty or self.changed():
            answer=messagebox.askyesnocancel('Değişiklikler','Filtre değişiklikleri kaydedilsin mi?',parent=self)
            if answer is None:return
            if answer:
                self.save()
                if self.dirty or self.changed():return
        self.destroy()
