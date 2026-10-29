"""CPR&B Map Patcher window: pick a Warband mod, then install or remove Colored Political Regions & Borders."""
import os, sys, threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cprb_patcher as cp

TITLE = "CPR&B Map Patcher %s" % cp.VERSION


class App(tk.Tk):
  def __init__(self):
    super().__init__()
    self.title(TITLE)
    try:
      self.iconbitmap(os.path.join(cp.resource_dir(), "cprb.ico"))
    except tk.TclError:
      pass
    self.geometry("760x560")
    self.minsize(620, 420)
    self.mods = []

    top = ttk.Frame(self, padding=10)
    top.pack(fill="x")
    ttk.Label(top, text="Colored Political Regions & Borders", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(top, text="Choose a Mount & Blade Warband mod, then install or remove the political map.",
              foreground="#555").pack(anchor="w")

    pick = ttk.Frame(self, padding=(10, 0))
    pick.pack(fill="x")
    self.combo = ttk.Combobox(pick, state="readonly")
    self.combo.pack(side="left", fill="x", expand=True)
    self.combo.bind("<<ComboboxSelected>>", lambda e: self.show_info())
    ttk.Button(pick, text="Browse...", command=self.browse).pack(side="left", padx=(6, 0))
    ttk.Button(pick, text="Refresh", command=self.refresh).pack(side="left", padx=(6, 0))

    self.info = tk.Text(self, height=14, wrap="word", font=("Consolas", 10), relief="flat", background="#f6f6f6")
    self.info.pack(fill="both", expand=True, padx=10, pady=10)
    self.info.configure(state="disabled")

    options = ttk.Frame(self, padding=(10, 0, 10, 6))
    options.pack(fill="x")
    ttk.Label(options, text="Map colour opacity for new games:").pack(side="left")
    self.opacity = tk.IntVar(value=cp.DEFAULT_OPACITY)
    ttk.Scale(options, from_=0, to=100, variable=self.opacity, length=220,
              command=lambda v: self.opacity.set(int(float(v)))).pack(side="left", padx=6)
    ttk.Label(options, textvariable=self.opacity, width=4).pack(side="left")
    ttk.Label(options, text="%   (change it in game with Ctrl+1)", foreground="#555").pack(side="left")

    buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
    buttons.pack(fill="x")
    self.install_button = ttk.Button(buttons, text="Install / Update", command=lambda: self.run(self.do_install))
    self.install_button.pack(side="left")
    self.uninstall_button = ttk.Button(buttons, text="Uninstall", command=lambda: self.run(self.do_uninstall))
    self.uninstall_button.pack(side="left", padx=(6, 0))
    ttk.Label(buttons, text="Tip: start a new game after installing. Workshop updates undo the patch; run it again.",
              foreground="#555").pack(side="right")
    self.refresh()

  # ------------------------------------------------------------------------------------------ helpers
  def write(self, text, clear=False):
    self.info.configure(state="normal")
    if clear:
      self.info.delete("1.0", "end")
    self.info.insert("end", text + "\n")
    self.info.see("end")
    self.info.configure(state="disabled")

  def selected_folder(self):
    i = self.combo.current()
    return self.mods[i][1] if 0 <= i < len(self.mods) else None

  def refresh(self, select=None):
    self.mods = cp.find_mods()
    if select and os.path.normcase(select) not in [os.path.normcase(f) for _, f in self.mods]:
      self.mods.append((os.path.basename(select), select))
    self.combo["values"] = [label for label, _ in self.mods]
    if self.mods:
      index = 0
      if select:
        index = [os.path.normcase(f) for _, f in self.mods].index(os.path.normcase(select))
      self.combo.current(index)
      self.show_info()
    else:
      self.write("No Warband mods found automatically. Use Browse... to pick a mod folder "
                 "(the folder that contains module.ini).", clear=True)

  def browse(self):
    folder = filedialog.askdirectory(title="Pick a mod folder (contains module.ini)")
    if folder:
      self.refresh(select=os.path.normpath(folder))

  def show_info(self):
    folder = self.selected_folder()
    if not folder:
      return
    try:
      self.write(cp.ModInfo(folder).summary(), clear=True)
    except cp.PatchError as e:
      self.write("ERROR: %s" % e, clear=True)

  def run(self, action):
    folder = self.selected_folder()
    if not folder:
      return
    self.install_button.configure(state="disabled")
    self.uninstall_button.configure(state="disabled")

    def work():
      try:
        action(folder)
      except cp.PatchError as e:
        self.after(0, lambda: messagebox.showerror(TITLE, str(e)))
      except Exception as e:  # keep the window alive and show what happened
        self.after(0, lambda: messagebox.showerror(TITLE, "Unexpected error: %r" % e))
      finally:
        self.after(0, self.finish)

    threading.Thread(target=work, daemon=True).start()

  def finish(self):
    self.install_button.configure(state="normal")
    self.uninstall_button.configure(state="normal")
    log = self.info.get("1.0", "end").strip().splitlines()[-1:]
    self.show_info()
    if log:
      self.write("\n" + log[0])

  def log(self, text):
    self.after(0, lambda: self.write(text))

  # ------------------------------------------------------------------------------------------ actions
  def do_install(self, folder):
    cp.install(folder, opacity=self.opacity.get(), log=self.log)

  def do_uninstall(self, folder):
    cp.uninstall(folder, log=self.log)


if __name__ == "__main__":
  App().mainloop()
