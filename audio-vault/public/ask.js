// Small input dialogs. The browser's own prompt() does not exist in the Windows app
// (Electron), so every page asks for text with these instead.
//   const name = await askText('Naam van de klok', 'Klok 1');           // string or null
//   const v = await askForm('Bewerken', [{ name: 'title', label: 'Titel', value: 'x' }]);  // object or null
(function () {
  function askForm(title, fields, { ok = 'Opslaan' } = {}) {
    return new Promise((resolve) => {
      const dlg = document.createElement('dialog');
      dlg.style.cssText = 'background:var(--panel,#18181b);color:var(--text,#f2f2f2);border:1px solid var(--line,#333);border-radius:12px;padding:20px 22px;width:min(460px,92vw);font:inherit';
      const form = document.createElement('form');
      form.method = 'dialog';
      const h = document.createElement('h3');
      h.textContent = title;
      h.style.cssText = 'margin:0 0 12px;font-size:16px';
      form.append(h);
      const inputs = fields.map((f) => {
        const label = document.createElement('label');
        label.textContent = f.label;
        label.style.cssText = 'display:block;font-size:12px;color:var(--muted,#999);margin:10px 0 4px';
        const input = document.createElement('input');
        input.type = 'text';
        input.name = f.name;
        input.value = f.value ?? '';
        if (f.placeholder) input.placeholder = f.placeholder;
        input.style.cssText = 'width:100%;box-sizing:border-box;background:rgba(127,127,127,.12);color:inherit;border:1px solid var(--line,#333);border-radius:6px;padding:8px;font:inherit';
        form.append(label, input);
        return input;
      });
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;margin-top:18px';
      const button = (text, value, primary) => {
        const b = document.createElement('button');
        b.textContent = text; b.value = value;
        b.style.cssText = `padding:8px 18px;border-radius:8px;cursor:pointer;font:inherit;border:1px solid ${primary ? 'var(--accent,#ff3b30)' : 'var(--line,#333)'};background:${primary ? 'var(--accent,#ff3b30)' : 'transparent'};color:${primary ? 'var(--accent-text,#000)' : 'inherit'}`;
        return b;
      };
      row.append(button('Annuleren', 'cancel', false), button(ok, 'ok', true));
      form.append(row);
      dlg.append(form);
      document.body.append(dlg);
      dlg.addEventListener('close', () => {
        const result = dlg.returnValue === 'ok' ? Object.fromEntries(inputs.map((i) => [i.name, i.value])) : null;
        dlg.remove();
        resolve(result);
      });
      // Enter in a field confirms.
      form.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); dlg.close('ok'); } });
      dlg.showModal();
      if (inputs[0]) { inputs[0].focus(); inputs[0].select(); }
    });
  }
  async function askText(label, value = '', options) {
    const r = await askForm(label, [{ name: 'v', label, value }], options);
    return r ? r.v : null;
  }
  window.askForm = askForm;
  window.askText = askText;
})();
