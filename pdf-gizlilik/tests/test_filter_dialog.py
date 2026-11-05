import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from filters import FilterStore

@unittest.skipUnless(sys.platform=='win32' or os.environ.get('DISPLAY'),'GUI display unavailable')
class DialogTests(unittest.TestCase):
    def test_add_save_reopen_edit_delete(self):
        import tkinter as tk
        from filter_dialog import FilterDialog
        with tempfile.TemporaryDirectory() as tmp:
            root=tk.Tk();root.withdraw();store=FilterStore(Path(tmp)/'filters.json');saved=[]
            try:
                d=FilterDialog(root,store,lambda:saved.append(True));root.update()
                d.name.set('Belediye kaydi');d.aliases.insert('1.0','Belediye sicil no');d.save()
                self.assertTrue(saved);self.assertEqual(store.data['user_rules'][0]['name'],'Belediye kaydi')
                d.close();d=FilterDialog(root,FilterStore(store.path),lambda:None);root.update()
                d.tree.selection_set('U1');d.select();d.name.set('Yeni ad');d.enabled.set(False);d.save()
                self.assertEqual(FilterStore(store.path).data['user_rules'][0]['name'],'Yeni ad')
                self.assertFalse(FilterStore(store.path).data['user_rules'][0]['enabled'])
                with patch('filter_dialog.messagebox.askyesno',return_value=True):d.delete()
                d.save();self.assertFalse(FilterStore(store.path).data['user_rules']);d.close()
            finally:root.destroy()
