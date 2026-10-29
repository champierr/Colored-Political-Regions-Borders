"""CPR&B Map Patcher: adds Colored Political Regions & Borders to an existing Mount & Blade Warband mod.

The political map lives in the world map terrain shaders. They need the position and owner of every center
and the colour of every faction each frame, so the patcher appends to the mod's compiled files:
  scripts.txt          script cprb_update_map: sends that data with set_shader_param_float4, and opens
                       the settings window on Ctrl+1
  simple_triggers.txt  a trigger calling it every frame
  presentations.txt    the settings window (map colour opacity slider)
  quick_strings.txt    shader parameter names and window texts
Everything is appended at the end, so no existing id moves. The shaders are installed as follows:
  - mod without shaders of its own: the CPR&B shaders are added (mb_2a.fxo, mb_2b.fxo)
  - mod with compiled shaders of its own (mb.fx or mb_2a/2b.fxo): only their four world map terrain
    techniques are swapped for the CPR&B ones, so the mod keeps everything else (waving flags, ...)
Originals are kept in <mod>/CPRB_backup and uninstalling removes exactly what was added.

Command line:  cprb_patcher.py info|install|uninstall <mod folder> [--opacity N]
"""
import hashlib, json, os, re, shutil, sys

if __package__ in (None, ""):
  sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fx_effect
from wb_compiler import compile_block, quick_string_line
from warband.header_common import *
from warband.header_operations import *
from warband.header_triggers import *
from warband.header_presentations import *

VERSION = "1.1.0"
SCRIPT_NAME = "cprb_update_map"
PRESENTATION_NAME = "cprb_settings"
BACKUP_DIR = "CPRB_backup"
MANIFEST = "install.json"
SHADER_FILES = ("mb_2a.fxo", "mb_2b.fxo")
MODULE_SHADER = "mb.fx"      # a compiled (or source) effect the game prefers over mb_2a/2b.fxo
MAX_FACTIONS = 96            # must match tools/build_shaders.py
DEFAULT_OPACITY = 60         # percent, used until the player changes it in game

# player troop slots used to keep the setting in the savegame and the window's overlay ids
SLOT_OPACITY = 7319          # opacity percent + 1 (0: never set)
SLOT_SLIDER, SLOT_VALUE_TEXT, SLOT_DONE = 7320, 7321, 7322
SLOT_JUST_OPENED = 7323      # 1 until the window's first frame, so the Ctrl+1 that opened it does not close it

# world map terrain techniques that carry the CPR&B code
MAP_TECHNIQUES = [base + suffix for base in ("diffuse_map", "diffuse_map_bump", "map_mountain", "map_mountain_bump")
                  for suffix in ("", "_SHDW", "_SHDWNVIDIA")]

CENTER_ID = re.compile(r"^p_(town|castle|village)_\d+$")


class PatchError(Exception):
  pass


def resource_dir():
  """Folder holding the shaders/ variants (next to this file, or inside the PyInstaller bundle)."""
  return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def shader_variants():
  base = os.path.join(resource_dir(), "shaders")
  sizes = []
  if os.path.isdir(base):
    for name in os.listdir(base):
      if name.isdigit() and all(os.path.isfile(os.path.join(base, name, f)) for f in SHADER_FILES):
        sizes.append(int(name))
  return sorted(sizes)


def _tag(tag, index):
  return (tag << op_num_value_bits) | index


# ------------------------------------------------------------------------------------- reading a mod
def _read_lines(path):
  with open(path, "r", encoding="latin-1", newline="") as f:
    return f.read().splitlines()


def _write_lines(path, lines):
  with open(path, "w", encoding="latin-1", newline="") as f:
    f.write("\r\n".join(lines) + "\r\n")


def _is_compiled_effect(path):
  with open(path, "rb") as f:
    return f.read(4) == b"\x01\x09\xff\xfe"


class ModInfo:
  REQUIRED = ("module.ini", "parties.txt", "factions.txt", "scripts.txt", "simple_triggers.txt",
              "quick_strings.txt", "presentations.txt", "meshes.txt")

  def __init__(self, folder):
    self.folder = os.path.abspath(folder)
    for name in self.REQUIRED:
      if not os.path.isfile(self.path(name)):
        raise PatchError("Not a Warband mod folder (missing %s): %s" % (name, self.folder))
    self.name = self._module_name()
    self.centers = self._centers()             # [(party index, x, y)]
    self.num_factions = int(_read_lines(self.path("factions.txt"))[1].split()[0])
    self.meshes = dict((line.split()[0], i) for i, line in enumerate(_read_lines(self.path("meshes.txt"))[1:])
                       if line.strip())
    self.installed = self._manifest()

  def path(self, name):
    return os.path.join(self.folder, name)

  def _module_name(self):
    for line in _read_lines(self.path("module.ini")):
      m = re.match(r"\s*module_name\s*=\s*(.+?)\s*$", line)
      if m:
        return m.group(1)
    return os.path.basename(self.folder)

  def _centers(self):
    centers = []
    for line in _read_lines(self.path("parties.txt"))[2:]:
      tokens = line.split()
      if len(tokens) > 15 and tokens[0] == "1" and CENTER_ID.match(tokens[3]):
        centers.append((int(tokens[1]), float(tokens[14]), float(tokens[15])))
    return centers

  def _manifest(self):
    path = os.path.join(self.folder, BACKUP_DIR, MANIFEST)
    if os.path.isfile(path):
      with open(path) as f:
        return json.load(f)
    return None

  def own_shaders(self):
    """Shader files of the mod itself (not counting ones CPR&B added)."""
    added = (self.installed or {}).get("shaders_added", [])
    if self.installed and "replaced_shaders" in self.installed:     # version 1.0.0 install
      added = [f for f in SHADER_FILES if f not in self.installed["replaced_shaders"]]
    return [f for f in (MODULE_SHADER,) + SHADER_FILES if os.path.isfile(self.path(f)) and f not in added]

  def shader_plan(self):
    """('add', files) to add the CPR&B shaders, ('merge', files) to patch the mod's own compiled shaders,
    or ('source', [mb.fx]) when the mod ships shader source, which the patcher cannot merge."""
    own = self.own_shaders()
    if MODULE_SHADER in own and not _is_compiled_effect(self.path(MODULE_SHADER)):
      return "source", [MODULE_SHADER]
    if own:
      return "merge", own
    return "add", list(SHADER_FILES)

  def problems(self):
    """Reasons the mod cannot be patched."""
    result = []
    if not self.centers:
      result.append("No towns, castles or villages found (party ids p_town_*, p_castle_*, p_village_*).")
    if self.num_factions > MAX_FACTIONS:
      result.append("The mod has %d factions; CPR&B supports up to %d." % (self.num_factions, MAX_FACTIONS))
    variants = shader_variants()
    if not variants:
      result.append("The patcher's shader files are missing.")
    elif len(self.centers) > variants[-1]:
      result.append("The mod has %d centers; CPR&B supports up to %d." % (len(self.centers), variants[-1]))
    if not self.installed and self.shader_plan()[0] == "source":
      result.append("The mod ships its shaders as source code (mb.fx), which the patcher cannot merge with.")
    return result

  def variant(self):
    return min(v for v in shader_variants() if v >= len(self.centers))

  def summary(self):
    lines = ["Mod: %s" % self.name,
             "Folder: %s" % self.folder,
             "Centers: %d, factions: %d" % (len(self.centers), self.num_factions),
             "CPR&B installed: %s" % ("yes (version %s)" % self.installed["version"] if self.installed else "no")]
    if not self.installed:
      plan, files = self.shader_plan()
      if plan == "merge":
        lines.append("The mod has its own shaders (%s): CPR&B will patch only their world map terrain part."
                     % ", ".join(files))
    lines += ["PROBLEM: " + p for p in self.problems()]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------- added content
def parameter_names(num_centers, num_factions):
  return (["cprbp%d" % k for k in range((num_centers + 1) // 2)] +
          ["cprbc%d" % k for k in range((num_factions + 3) // 4)] + ["cprbopacity", "cprbunit"])


def _quantize(coordinate):
  """Map coordinate -> shader units: round((c + 2048) * 4), kept away from the ends of the 14 bit range."""
  return max(1, min(16382, int(round((coordinate + 2048.0) * 4.0))))


def update_script_ops(centers, num_factions, default_opacity):
  """script cprb_update_map, run every frame."""
  ops = []
  # centers, two per parameter: (owner * 16384 + xq, yq, owner * 16384 + xq, yq)
  for k in range((len(centers) + 1) // 2):
    values = []
    for local, i in ((":owner_a", 2 * k), (":owner_b", 2 * k + 1)):
      if i < len(centers):
        party, x, y = centers[i]
        ops += [(store_faction_of_party, local, _tag(tag_party, party)),
                (val_mul, local, 16384),
                (val_add, local, _quantize(x))]
        values += [local, _quantize(y)]
      else:
        values += [0, 0]
    ops.append((set_shader_param_float4, "@cprbp%d" % k) + tuple(values))
  # faction colours, four per parameter: 7 bits per channel, red * 16384 + green * 128 + blue
  for g in range((num_factions + 3) // 4):
    for j in range(4):
      f = 4 * g + j
      slot = ":colour_%d" % j
      if f < num_factions:
        ops += [(faction_get_color, ":colour", f),
                (store_div, ":red", ":colour", 0x20000), (val_mod, ":red", 128), (val_mul, ":red", 16384),
                (store_div, ":green", ":colour", 0x200), (val_mod, ":green", 128), (val_mul, ":green", 128),
                (store_div, ":blue", ":colour", 2), (val_mod, ":blue", 128),
                (val_add, ":red", ":green"), (val_add, ":red", ":blue"),
                (assign, slot, ":red")]
      else:
        ops.append((assign, slot, 0))
    ops.append((set_shader_param_float4, "@cprbc%d" % g, ":colour_0", ":colour_1", ":colour_2", ":colour_3"))
  # opacity in percent, kept in the savegame (player troop slot, stored + 1 so 0 means never set)
  ops += [(troop_get_slot, ":opacity", "trp_player", SLOT_OPACITY),
          (try_begin),
            (le, ":opacity", 0),
            (assign, ":opacity", default_opacity + 1),
          (try_end),
          (val_sub, ":opacity", 1),
          (set_shader_param_float, "@cprbopacity", ":opacity"),
          (set_shader_param_float, "@cprbunit", 1)]
  # Ctrl+1 opens the settings window
  ops += [(try_begin),
            (this_or_next|key_is_down, key_left_control),
            (key_is_down, key_right_control),
            (key_clicked, key_1),
            (neg|is_presentation_active, "prsnt_" + PRESENTATION_NAME),
            (start_presentation, "prsnt_" + PRESENTATION_NAME),
          (try_end)]
  return ops


def _place(overlay, x, y, size=None):
  ops = [(position_set_x, pos1, x), (position_set_y, pos1, y), (overlay_set_position, overlay, pos1)]
  if size:
    ops += [(position_set_x, pos1, size[0]), (position_set_y, pos1, size[1]), (overlay_set_size, overlay, pos1)]
  return ops


def _text(overlay, text, x, y, scale, colour):
  ops = [(create_text_overlay, overlay, text, tf_left_align)] + _place(overlay, x, y, (scale, scale))
  return ops + ([(overlay_set_color, overlay, colour)] if colour is not None else [])


def settings_presentation(default_opacity, has_panel_mesh):
  """The settings window: a small panel in the top left corner with the map colour opacity slider.
  Screen units are 1000 wide and 750 high. A slider's bar is drawn about 16 units above its position."""
  white = 0xFFFFFF if has_panel_mesh else None            # light text needs the dark panel behind it
  grey = 0xBBBBBB if has_panel_mesh else None
  load = [(set_fixed_point_multiplier, 1000)]
  if has_panel_mesh:
    # mesh_white_plane is 20 screen units at size 1000
    load += [(create_mesh_overlay, reg1, "mesh_white_plane"),
             (overlay_set_color, reg1, 0x000000),
             (overlay_set_alpha, reg1, 0xC0)] + _place(reg1, 10, 540, (18000, 10000))
  load += _text(reg1, "@Colored Political Regions & Borders", 25, 712, 850, white)
  load += _text(reg1, "@Map colour opacity", 25, 680, 750, white)
  load += [(troop_get_slot, ":opacity", "trp_player", SLOT_OPACITY),
           (try_begin),
             (le, ":opacity", 0),
             (assign, ":opacity", default_opacity + 1),
           (try_end),
           (val_sub, ":opacity", 1),
           (create_slider_overlay, ":slider", 0, 100),
           (overlay_set_val, ":slider", ":opacity"),
           (troop_set_slot, "trp_player", SLOT_SLIDER, ":slider")] + _place(":slider", 135, 630, (900, 1000))
  load += [(assign, reg1, ":opacity")] + _text(":value", "@{reg1}%", 255, 624, 800, white)
  load += [(troop_set_slot, "trp_player", SLOT_VALUE_TEXT, ":value")]
  load += _text(reg1, "@Ctrl+1 or Esc closes. Saved with your game.", 25, 586, 650, grey)
  load += [(create_button_overlay, ":done", "@Done", 0),
           (troop_set_slot, "trp_player", SLOT_DONE, ":done")] + _place(":done", 25, 552, (850, 850))
  if white is not None:
    load += [(overlay_set_color, ":done", white)]
  load += [(troop_set_slot, "trp_player", SLOT_JUST_OPENED, 1),
           (presentation_set_duration, 999999)]

  change = [(store_trigger_param_1, ":object"),
            (store_trigger_param_2, ":value"),
            (try_begin),
              (troop_slot_eq, "trp_player", SLOT_SLIDER, ":object"),
              (store_add, ":stored", ":value", 1),
              (troop_set_slot, "trp_player", SLOT_OPACITY, ":stored"),
              (assign, reg1, ":value"),
              (troop_get_slot, ":text", "trp_player", SLOT_VALUE_TEXT),
              (overlay_set_text, ":text", "@{reg1}%"),
              (call_script, "script_" + SCRIPT_NAME),
            (else_try),
              (troop_slot_eq, "trp_player", SLOT_DONE, ":object"),
              (presentation_set_duration, 0),
            (try_end)]

  # Esc or Ctrl+1 again closes the window
  run = [(try_begin),
           (troop_slot_eq, "trp_player", SLOT_JUST_OPENED, 1),
           (troop_set_slot, "trp_player", SLOT_JUST_OPENED, 0),
         (else_try),
           (this_or_next|key_clicked, key_escape),
           (key_clicked, key_1),
           (presentation_set_duration, 0),
         (try_end)]
  return (prsntf_manual_end_only, 0, [(ti_on_presentation_load, load),
                                      (ti_on_presentation_event_state_change, change),
                                      (ti_on_presentation_run, run)])


# ---------------------------------------------------------------------------- appending to text files
class Appender:
  """Lines added at the end of a compiled text file whose entry count sits on line count_line."""

  def __init__(self, mod, name, count_line):
    self.mod, self.name, self.count_line = mod, name, count_line
    self.lines = _read_lines(mod.path(name))
    self.count_text = self.lines[count_line]
    self.count = int(self.count_text)
    self.lines_before = len(self.lines)
    self.added = 0

  def append(self, lines, entries=1):
    self.lines += lines
    self.added += entries
    return self.count + self.added - 1                # index of the (last) new entry

  def save(self):
    self.lines[self.count_line] = self.count_text.replace(str(self.count), str(self.count + self.added))
    _write_lines(self.mod.path(self.name), self.lines)
    return {"count_line": self.count_line, "count_text": self.count_text, "lines_before": self.lines_before,
            "added_hash": hashlib.sha1("\n".join(self.lines[self.lines_before:]).encode("latin-1")).hexdigest()}


def _remove_appended(mod, name, record):
  lines = _read_lines(mod.path(name))
  added = "\n".join(lines[record["lines_before"]:]).encode("latin-1")
  if hashlib.sha1(added).hexdigest() != record["added_hash"]:
    raise PatchError("%s changed after CPR&B was installed. Restore it from %s by hand."
                     % (name, os.path.join(mod.folder, BACKUP_DIR)))
  lines = lines[:record["lines_before"]]
  lines[record["count_line"]] = record["count_text"]
  _write_lines(mod.path(name), lines)


# -------------------------------------------------------------------------------------------- shaders
def merge_map_shaders(target, ours):
  """Puts the CPR&B world map terrain shaders and their parameters into another compiled effect."""
  target_names, our_names = target.technique_names(), ours.technique_names()
  swapped = 0
  for name in MAP_TECHNIQUES:
    if name not in target_names:
      continue
    t_res = target.shader_resources(target_names.index(name))
    o_res = ours.shader_resources(our_names.index(name))
    if sorted(t_res) != sorted(o_res):
      raise PatchError("The mod's %s technique is built differently; cannot patch it." % name)
    for key, index in t_res.items():
      target.set_resource_data(index, ours.resources[o_res[key]][5])
    swapped += 1
  if not swapped:
    raise PatchError("The mod's shaders have no world map terrain techniques.")
  existing = set(target.param_names())
  for i, name in enumerate(ours.param_names()):
    if name not in existing:
      target.add_param(ours.param_definition(i))
  return swapped


def prepare_shaders(mod, variant):
  """(plan, {file name: new contents}) without touching the mod: the CPR&B shaders to add, or the mod's
  own compiled shaders with the CPR&B world map terrain techniques swapped in."""
  plan, files = mod.shader_plan()
  source = os.path.join(resource_dir(), "shaders", str(variant))
  result = {}
  for name in files:
    if plan == "add":
      with open(os.path.join(source, name), "rb") as f:
        result[name] = f.read()
    else:
      target = fx_effect.load(mod.path(name))
      ours = fx_effect.load(os.path.join(source, "mb_2b.fxo" if name == "mb_2b.fxo" else "mb_2a.fxo"))
      merge_map_shaders(target, ours)
      result[name] = target.to_bytes()
      fx_effect.Effect(result[name])                # must read back
  return plan, result


# ---------------------------------------------------------------------------------- install/uninstall
def install(folder, opacity=DEFAULT_OPACITY, log=print):
  mod = ModInfo(folder)
  if mod.installed:
    log("Removing the previous CPR&B install first.")
    uninstall(folder, log=log)
    mod = ModInfo(folder)
  problems = mod.problems()
  if problems:
    raise PatchError("\n".join(problems))
  opacity = max(0, min(100, int(opacity)))

  # prepare everything first, so a failure leaves the mod untouched
  texts = {}
  for name in ("scripts.txt", "simple_triggers.txt", "quick_strings.txt", "presentations.txt"):
    texts[name] = Appender(mod, name, 0 if name == "quick_strings.txt" else 1)
  scripts, triggers, quick, presentations = (texts[n] for n in ("scripts.txt", "simple_triggers.txt",
                                                                 "quick_strings.txt", "presentations.txt"))
  if any(line.startswith(SCRIPT_NAME + " ") for line in scripts.lines):
    raise PatchError("scripts.txt already contains %s but no install record was found." % SCRIPT_NAME)

  script_id = scripts.count
  presentation_id = presentations.count
  quick_ids = {}

  def resolve(symbol):
    if symbol.startswith("@"):
      if symbol not in quick_ids:
        quick_ids[symbol] = quick.append([quick_string_line(symbol[1:])])
      return _tag(tag_quick_string, quick_ids[symbol])
    if symbol == "script_" + SCRIPT_NAME:
      return _tag(tag_script, script_id)
    if symbol == "prsnt_" + PRESENTATION_NAME:
      return _tag(tag_presentation, presentation_id)
    if symbol == "trp_player":
      return _tag(tag_troop, 0)
    if symbol.startswith("mesh_") and symbol in mod.meshes:
      return _tag(tag_mesh, mod.meshes[symbol])
    raise PatchError("Unknown identifier %s" % symbol)

  # shader parameter names first, in a fixed order
  for name in parameter_names(len(mod.centers), mod.num_factions):
    resolve("@" + name)
  scripts.append(["%s -1" % SCRIPT_NAME,
                  compile_block(update_script_ops(mod.centers, mod.num_factions, opacity), resolve)])
  triggers.append(["%f %s" % (0.0, compile_block([(call_script, "script_" + SCRIPT_NAME)], resolve))])
  flags, mesh, presentation_triggers = settings_presentation(opacity, "mesh_white_plane" in mod.meshes)
  block = ["prsnt_%s %d %d %d" % (PRESENTATION_NAME, flags, mesh, len(presentation_triggers))]
  block += ["%f %s" % (interval, compile_block(ops, resolve)) for interval, ops in presentation_triggers]
  presentations.append(block + ["", ""])

  variant = mod.variant()
  plan, shaders = prepare_shaders(mod, variant)

  backup = os.path.join(mod.folder, BACKUP_DIR)
  os.makedirs(backup, exist_ok=True)
  for name in list(texts) + (list(shaders) if plan == "merge" else []):
    shutil.copy2(mod.path(name), os.path.join(backup, name))
  manifest = {"version": VERSION, "shader_variant": variant, "opacity": opacity,
              "files": dict((name, appender.save()) for name, appender in texts.items()),
              "shaders_added": list(shaders) if plan == "add" else [],
              "shaders_merged": list(shaders) if plan == "merge" else []}
  for name, data in shaders.items():
    with open(mod.path(name), "wb") as f:
      f.write(data)
  if plan == "merge":
    log("Patched only the world map part of the mod's own shaders (%s)." % ", ".join(shaders))
  with open(os.path.join(backup, MANIFEST), "w") as f:
    json.dump(manifest, f, indent=2)
  log("Installed CPR&B %s into %s: %d centers, %d factions, shader size %d, default opacity %d%%."
      % (VERSION, mod.name, len(mod.centers), mod.num_factions, variant, opacity))
  return manifest


def _uninstall_v1(mod, m, backup):
  """Removes a version 1.0.0 install (script, trigger and quick strings at the end of their files)."""
  scripts = _read_lines(mod.path("scripts.txt"))
  if int(scripts[1]) != m["script_id"] + 1 or scripts[2 + 2 * m["script_id"]] != "%s -1" % SCRIPT_NAME:
    raise PatchError("scripts.txt changed after CPR&B was installed. Restore it from %s by hand." % backup)
  triggers = _read_lines(mod.path("simple_triggers.txt"))
  if int(triggers[1]) != m["trigger_id"] + 1:
    raise PatchError("simple_triggers.txt changed after CPR&B was installed. Restore it from %s by hand." % backup)
  qs = _read_lines(mod.path("quick_strings.txt"))
  if int(qs[0]) != m["first_qstr"] + m["num_qstr"]:
    raise PatchError("quick_strings.txt changed after CPR&B was installed. Restore it from %s by hand." % backup)
  scripts = scripts[:2 + 2 * m["script_id"]]
  scripts[1] = str(m["script_id"])
  triggers = triggers[:2 + m["trigger_id"]]
  triggers[1] = str(m["trigger_id"])
  qs = qs[:1 + m["first_qstr"]]
  qs[0] = str(m["first_qstr"])
  _write_lines(mod.path("scripts.txt"), scripts)
  _write_lines(mod.path("simple_triggers.txt"), triggers)
  _write_lines(mod.path("quick_strings.txt"), qs)
  for name in SHADER_FILES:
    if name in m["replaced_shaders"]:
      shutil.copy2(os.path.join(backup, name), mod.path(name))
    elif os.path.isfile(mod.path(name)):
      os.remove(mod.path(name))


def uninstall(folder, log=print):
  mod = ModInfo(folder)
  m = mod.installed
  if not m:
    raise PatchError("CPR&B is not installed in this mod.")
  backup = os.path.join(mod.folder, BACKUP_DIR)
  if "files" not in m:
    _uninstall_v1(mod, m, backup)
  else:
    for name, record in m["files"].items():          # check everything before changing anything
      lines = _read_lines(mod.path(name))
      if hashlib.sha1("\n".join(lines[record["lines_before"]:]).encode("latin-1")).hexdigest() != record["added_hash"]:
        raise PatchError("%s changed after CPR&B was installed. Restore the files from %s by hand." % (name, backup))
    for name, record in m["files"].items():
      _remove_appended(mod, name, record)
    for name in m["shaders_merged"]:
      shutil.copy2(os.path.join(backup, name), mod.path(name))
    for name in m["shaders_added"]:
      if os.path.isfile(mod.path(name)):
        os.remove(mod.path(name))
  shutil.rmtree(backup)
  log("Removed CPR&B from %s." % mod.name)


# ---------------------------------------------------------------------------------------- finding mods
def find_game_folders():
  """Warband install folders from the Steam libraries, best effort."""
  steam_roots = []
  try:
    import winreg
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")):
      try:
        with winreg.OpenKey(hive, key) as k:
          for value in ("InstallPath", "SteamPath"):
            try:
              steam_roots.append(winreg.QueryValueEx(k, value)[0])
            except OSError:
              pass
      except OSError:
        pass
  except ImportError:
    pass
  libraries = []
  for root in steam_roots:
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    libraries.append(os.path.join(root, "steamapps"))
    if os.path.isfile(vdf):
      for path in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8", errors="ignore").read()):
        libraries.append(os.path.join(path.replace("\\\\", "\\"), "steamapps"))
  result = []
  for lib in libraries:
    game = os.path.join(lib, "common", "MountBlade Warband")
    if os.path.isdir(game) and os.path.normcase(game) not in [os.path.normcase(g) for g, _ in result]:
      result.append((game, os.path.join(lib, "workshop", "content", "48700")))
  return result


def find_mods():
  """[(display name, folder)] for every mod in every Warband install and its Workshop folder."""
  mods = []
  for game, workshop in find_game_folders():
    for base in (os.path.join(game, "Modules"), workshop):
      if not os.path.isdir(base):
        continue
      for entry in sorted(os.listdir(base)):
        folder = os.path.join(base, entry)
        if os.path.isfile(os.path.join(folder, "module.ini")) and os.path.isfile(os.path.join(folder, "scripts.txt")):
          label = entry
          if base == workshop:
            try:
              label = "%s (Workshop %s)" % (ModInfo(folder).name, entry)
            except PatchError:
              label = "Workshop %s" % entry
          mods.append((label, folder))
  return mods


def main(argv):
  if len(argv) < 3 or argv[1] not in ("info", "install", "uninstall"):
    print(__doc__)
    return 2
  try:
    if argv[1] == "info":
      print(ModInfo(argv[2]).summary())
    elif argv[1] == "install":
      opacity = int(argv[argv.index("--opacity") + 1]) if "--opacity" in argv else DEFAULT_OPACITY
      install(argv[2], opacity=opacity)
    else:
      uninstall(argv[2])
  except PatchError as e:
    print("ERROR: %s" % e)
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
