"""CPR&B Map Patcher: adds Colored Political Regions & Borders to an existing Mount & Blade Warband mod.

The political map lives in the world map terrain shaders. They need the position and owner of every center
and the colour of every faction each frame, so the patcher appends to the mod's compiled files:
  scripts.txt          script cprb_update_map: sends that data with set_shader_param_float4, keeps the
                       player's kingdom in the colour chosen in game, and opens the windows on Ctrl+1 and
                       Ctrl+2; script cprb_hsl_to_rgb for the colour picker
  simple_triggers.txt  a trigger calling cprb_update_map every frame
  presentations.txt    the settings window (Ctrl+1, map colour opacity) and the kingdom colour picker (Ctrl+2)
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

VERSION = "1.2.0"
SCRIPT_NAME = "cprb_update_map"
HSL_SCRIPT = "cprb_hsl_to_rgb"
PRESENTATION_NAME = "cprb_settings"
PICKER_NAME = "cprb_kingdom_colour"
PLAYER_KINGDOM = "fac_player_supporters_faction"   # the kingdom the player founds (Native and most mods)
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
# the player's kingdom colour (Ctrl+2), kept in the savegame like the opacity
SLOT_KINGDOM_COLOUR = 7324   # chosen colour + 1 (0: never chosen, the mod's own colour stays)
SLOT_PREVIEW_COLOUR = 7325   # colour + 1 shown while the picker is open (0: none)
SLOT_OLD_COLOUR = 7326       # the kingdom's colour when the picker opened (-1: no kingdom)
SLOT_HUE, SLOT_SAT, SLOT_LIGHT = 7327, 7328, 7329    # picker state: 0-359, 0-1000, 0-1000
SLOT_DRAG = 7330             # 0, 1 while dragging in the colour field, 2 in the lightness bar
SLOT_FIELD_MARK, SLOT_BAR_MARK, SLOT_SWATCH, SLOT_RGB_TEXT = 7331, 7332, 7333, 7334
SLOT_PICK_DONE, SLOT_PICK_CANCEL, SLOT_PICK_OPENED = 7335, 7336, 7337
SLOT_FIELD_MARK_IN, SLOT_BAR_MARK_IN = 7338, 7339
SLOT_BAR_CELLS = 7340        # 7340.. the overlay of every lightness bar cell
SLOT_OPACITY_PREVIEW = 7360  # opacity + 1 shown while the settings window is open (0: none); Done saves it
SLOT_CANCEL = 7361           # the settings window's Cancel button
SLOT_OPACITY_DRAG = 7363     # 1 while dragging the opacity bar
SLOT_REOPEN = 7364           # frames until a window closed to show the map opens again (0: none)
SLOT_REOPEN_WHICH = 7365     # that window: 1 settings, 2 kingdom colour; read and cleared when it opens
SLOT_PRESSED = 7366          # the button slot the left mouse button went down on (0: none)
SLOT_SHOWN_OPACITY, SLOT_SHOWN_COLOUR = 7367, 7368   # value + 1 the map was last drawn with

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
    self.factions = self._factions()           # {"fac_...": index}
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

  def _factions(self):
    """Faction ids in file order; each faction starts with a line "[0] fac_<id> <name> <flags> <colour>"."""
    result = {}
    for line in _read_lines(self.path("factions.txt"))[2:]:
      for token in line.split()[:2]:
        if token.startswith("fac_"):
          result.setdefault(token, len(result))
          break
    return result

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
    if "mesh_white_plane" not in self.meshes:
      result.append("The mod has no mesh_white_plane, which the CPR&B windows are drawn with.")
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


def kingdom_colour_ops(kingdom):
  """The player's kingdom in the colour shown in the picker, or else the one chosen with it, if any."""
  if kingdom is None:
    return []
  return [(troop_get_slot, ":want", "trp_player", SLOT_PREVIEW_COLOUR),
          (try_begin),
            (le, ":want", 0),
            (troop_get_slot, ":want", "trp_player", SLOT_KINGDOM_COLOUR),
          (try_end),
          (try_begin),
            (gt, ":want", 0),
            (val_sub, ":want", 1),
            (faction_get_color, ":now", kingdom),
            (neq, ":now", ":want"),
            (faction_set_color, kingdom, ":want"),
          (try_end)]


def update_script_ops(centers, num_factions, default_opacity, kingdom):
  """script cprb_update_map, run every frame."""
  ops = kingdom_colour_ops(kingdom)          # first, so the shader gets the colour in the same frame
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
  # opacity in percent: the one shown in the settings window, or else the saved one (player troop slots,
  # stored + 1 so 0 means not set), or else the default
  ops += [(troop_get_slot, ":opacity", "trp_player", SLOT_OPACITY_PREVIEW),
          (try_begin),
            (le, ":opacity", 0),
            (troop_get_slot, ":opacity", "trp_player", SLOT_OPACITY),
          (try_end),
          (try_begin),
            (le, ":opacity", 0),
            (assign, ":opacity", default_opacity + 1),
          (try_end),
          (val_sub, ":opacity", 1),
          (set_shader_param_float, "@cprbopacity", ":opacity"),
          (set_shader_param_float, "@cprbunit", 1)]
  # Ctrl+1 opens the settings window, Ctrl+2 the kingdom colour picker
  for key, name in ((key_1, PRESENTATION_NAME), (key_2, PICKER_NAME)):
    ops += [(try_begin),
              (this_or_next|key_is_down, key_left_control),
              (key_is_down, key_right_control),
              (key_clicked, key),
              (neg|is_presentation_active, "prsnt_" + PRESENTATION_NAME),
              (neg|is_presentation_active, "prsnt_" + PICKER_NAME),
              (start_presentation, "prsnt_" + name),
            (try_end)]
  # a window closed for a moment so the game draws the map with the new values opens again (_show_on_map)
  ops += [(troop_get_slot, ":reopen", "trp_player", SLOT_REOPEN),
          (try_begin),
            (gt, ":reopen", 0),
            (neg|is_presentation_active, "prsnt_" + PRESENTATION_NAME),
            (neg|is_presentation_active, "prsnt_" + PICKER_NAME),
            (val_sub, ":reopen", 1),
            (troop_set_slot, "trp_player", SLOT_REOPEN, ":reopen"),
            (eq, ":reopen", 0),
            (try_begin),
              (troop_slot_eq, "trp_player", SLOT_REOPEN_WHICH, 1),
              (start_presentation, "prsnt_" + PRESENTATION_NAME),
            (else_try),
              (start_presentation, "prsnt_" + PICKER_NAME),
            (try_end),
          (try_end)]
  return ops


def hsl_script_ops():
  """script cprb_hsl_to_rgb (hue 0-359, saturation 0-1000, lightness 0-1000): reg0 = 0xRRGGBB."""
  ops = [(store_script_param, ":h", 1),
         (store_script_param, ":s", 2),
         (store_script_param, ":l", 3),
         # chroma c = (1 - |2l - 1|) s, x = c (1 - |(h / 60) mod 2 - 1|), m = l - c / 2   (all * 1000)
         (store_mul, ":c", ":l", 2), (val_sub, ":c", 1000), (val_abs, ":c"),
         (store_sub, ":c", 1000, ":c"), (val_mul, ":c", ":s"), (val_div, ":c", 1000),
         (store_mul, ":x", ":h", 1000), (val_div, ":x", 60), (val_mod, ":x", 2000), (val_sub, ":x", 1000),
         (val_abs, ":x"), (store_sub, ":x", 1000, ":x"), (val_mul, ":x", ":c"), (val_div, ":x", 1000),
         (store_div, ":m", ":c", 2), (store_sub, ":m", ":l", ":m"),
         (store_div, ":sector", ":h", 60),
         (assign, ":r", 0), (assign, ":g", 0), (assign, ":b", 0)]
  sectors = [(":c", ":x", None), (":x", ":c", None), (None, ":c", ":x"),
             (None, ":x", ":c"), (":x", None, ":c"), (":c", None, ":x")]
  for n, (r, g, b) in enumerate(sectors):
    ops += [(else_try) if n else (try_begin)]
    if n < 5:
      ops.append((eq, ":sector", n))
    for channel, value in ((":r", r), (":g", g), (":b", b)):
      if value:
        ops.append((assign, channel, value))
  ops.append(try_end)
  for channel in (":r", ":g", ":b"):
    ops += [(val_add, channel, ":m"), (val_mul, channel, 255), (val_add, channel, 500), (val_div, channel, 1000),
            (val_clamp, channel, 0, 256)]
  ops += [(store_mul, reg0, ":r", 0x10000), (val_mul, ":g", 0x100), (val_add, reg0, ":g"), (val_add, reg0, ":b")]
  return ops


def _place(overlay, x, y, size=None):
  ops = [(position_set_x, pos1, x), (position_set_y, pos1, y), (overlay_set_position, overlay, pos1)]
  if size:
    ops += [(position_set_x, pos1, size[0]), (position_set_y, pos1, size[1]), (overlay_set_size, overlay, pos1)]
  return ops


def _text(overlay, text, x, y, scale, colour):
  ops = [(create_text_overlay, overlay, text, tf_left_align)] + _place(overlay, x, y, (scale, scale))
  return ops + ([(overlay_set_color, overlay, colour)] if colour is not None else [])


PARCHMENT, FRAME = 0xE6D5B0, 0x4B3621       # the colours of the game's own windows
TITLE, HINT = 0x3B2412, 0x5C4A36


def _plane(overlay, x, y, w, h, colour, alpha=0xFF):
  """A flat rectangle (mesh_white_plane is 20 screen units at size 1000) with its lower left corner at x, y."""
  return ([(create_mesh_overlay, overlay, "mesh_white_plane"),
           (overlay_set_color, overlay, colour),
           (overlay_set_alpha, overlay, alpha)] + _place(overlay, x, y, (w * 50, h * 50)))


def _panel(x0, y0, x1, y1):
  """A parchment panel with a dark brown frame, like the game's windows."""
  return (_plane(reg1, x0 - 2, y0 - 2, x1 - x0 + 4, y1 - y0 + 4, FRAME) +
          _plane(reg1, x0, y0, x1 - x0, y1 - y0, PARCHMENT, 0xF2))


# While one of these windows is open the game does not draw the world map again; it keeps showing the
# last picture (the fps jump, and a new colour only showed after Done). Read-only windows do not help and
# let clicks through to the map. So when the mouse is let go after a change, the window closes for a few
# frames (_show_on_map): the map is drawn with the new values and the map script opens the window again
# as it was. The windows read the mouse in their run trigger (buttons, the opacity bar, the colour field).
WINDOW_FLAGS = prsntf_manual_end_only
WINDOW_SETTINGS, WINDOW_PICKER = 1, 2
BUTTON, BUTTON_HOVER = 0x3B2412, 0xA0661E
# Frames the window stays closed. The picture behind the open window is whatever each of the screen's
# two or three buffers last held: with two frames only one of them got the new map, and the old and new
# opacity then flickered until Done. Four leaves at least three drawn frames.
SHOW_FRAMES = 4


def _show_on_map(window):
  """Closes the window for a few frames so the map is drawn with the new values; it then opens again."""
  return [(troop_set_slot, "trp_player", SLOT_REOPEN_WHICH, window),
          (troop_set_slot, "trp_player", SLOT_REOPEN, SHOW_FRAMES),
          (presentation_set_duration, 0)]


def _reopened_ops(window):
  """":reopened" = 1 when the window opens again after _show_on_map (it then keeps its state)."""
  return [(assign, ":reopened", 0),
          (try_begin),
            (troop_slot_eq, "trp_player", SLOT_REOPEN_WHICH, window),
            (assign, ":reopened", 1),
            (troop_set_slot, "trp_player", SLOT_REOPEN_WHICH, 0),
          (try_end)]


def _button_text(slot, text, x, y):
  """A clickable word; _button_run makes it work."""
  return _text(":button", text, x, y, 850, BUTTON) + [(troop_set_slot, "trp_player", slot, ":button")]


def _button_run(slot, x, y, width, action):
  """Run trigger part of a _button_text: lights up under the mouse; pressing and letting go on it runs
  action. (Acting on the press would close the window with the button still down, and letting it go
  would then reach the map and send the party there.) _release_ops must end the run trigger."""
  return [(troop_get_slot, ":button", "trp_player", slot),
          (try_begin),
            (is_between, ":mx", x - 6, x + width + 6),
            (is_between, ":my", y - 8, y + 24),
            (overlay_set_color, ":button", BUTTON_HOVER),
            (try_begin),
              (key_clicked, key_left_mouse_button),
              (troop_set_slot, "trp_player", SLOT_PRESSED, slot),
            (else_try),
              (neg|key_is_down, key_left_mouse_button),
              (troop_slot_eq, "trp_player", SLOT_PRESSED, slot),
              (troop_set_slot, "trp_player", SLOT_PRESSED, 0)] + action + [
            (try_end),
          (else_try),
            (overlay_set_color, ":button", BUTTON),
          (try_end)]


def _release_ops():
  """End of a run trigger: a press let go anywhere else no longer counts for a button."""
  return [(try_begin),
            (neg|key_is_down, key_left_mouse_button),
            (troop_set_slot, "trp_player", SLOT_PRESSED, 0),
          (try_end)]


# the opacity bar of the settings window
TRACK_X, TRACK_Y, TRACK_W, TRACK_H = 25, 640, 280, 8


def _opacity_view_ops(value):
  """Shows an opacity (0-100) with the bar's handle and the percent text. Overlays are only moved here,
  like the picker's markers (an earlier filled bar resized every frame, and that window flickered)."""
  return [(store_mul, ":w", value, TRACK_W), (val_div, ":w", 100),
          (troop_get_slot, ":handle", "trp_player", SLOT_SLIDER),
          (store_add, ":x", ":w", TRACK_X - 3),
          (position_set_x, pos1, ":x"), (position_set_y, pos1, TRACK_Y - 5), (overlay_set_position, ":handle", pos1),
          (assign, reg1, value),
          (troop_get_slot, ":text", "trp_player", SLOT_VALUE_TEXT),
          (overlay_set_text, ":text", "@{reg1}%")]


def settings_presentation(default_opacity):
  """The settings window: a small panel in the top left corner with the map colour opacity bar. Letting
  go of the bar shows the value on the map; Done saves, Cancel, Esc or Ctrl+1 again go back to the saved
  value. Screen units are 1000 wide and 750 high."""
  player = "trp_player"
  load = [(set_fixed_point_multiplier, 1000)] + _reopened_ops(WINDOW_SETTINGS) + _panel(10, 540, 370, 740)
  load += _text(reg1, "@Colored Political Regions & Borders", 25, 712, 850, TITLE)
  load += _text(reg1, "@Map colour opacity", 25, 680, 750, 0x000000)
  load += _plane(reg1, TRACK_X - 1, TRACK_Y - 1, TRACK_W + 2, TRACK_H + 2, FRAME)
  load += _plane(reg1, TRACK_X, TRACK_Y, TRACK_W, TRACK_H, 0xCBB68C)
  load += _plane(":handle", 0, 0, 6, TRACK_H + 10, FRAME) + [(troop_set_slot, player, SLOT_SLIDER, ":handle")]
  load += _text(":value", "@{reg1}%", TRACK_X + TRACK_W + 14, TRACK_Y - 4, 800, 0x000000)
  load += [(troop_set_slot, player, SLOT_VALUE_TEXT, ":value")]
  load += _text(reg1, "@Let go of the bar to see it on the map. Done saves it.", 25, 594, 650, HINT)
  load += _text(reg1, "@Esc cancels.   Ctrl+2: your kingdom's colour.", 25, 574, 650, HINT)
  load += _button_text(SLOT_DONE, "@Done", 25, 548) + _button_text(SLOT_CANCEL, "@Cancel", 95, 548)
  # the saved opacity, or when opening again after _show_on_map the one being tried
  load += [(troop_get_slot, ":opacity", player, SLOT_OPACITY),
           (try_begin),
             (le, ":opacity", 0),
             (assign, ":opacity", default_opacity + 1),
           (try_end),
           (try_begin),
             (eq, ":reopened", 1),
             (troop_slot_ge, player, SLOT_OPACITY_PREVIEW, 1),
             (troop_get_slot, ":opacity", player, SLOT_OPACITY_PREVIEW),
           (else_try),
             (eq, ":reopened", 0),
             (troop_set_slot, player, SLOT_OPACITY_PREVIEW, 0),
             (troop_set_slot, player, SLOT_SHOWN_OPACITY, ":opacity"),
           (try_end),
           (val_sub, ":opacity", 1)] + _opacity_view_ops(":opacity")
  load += [(troop_set_slot, player, SLOT_OPACITY_DRAG, 0),
           (troop_set_slot, player, SLOT_PRESSED, 0),
           (troop_set_slot, player, SLOT_JUST_OPENED, 1),
           (presentation_set_duration, 999999)]

  cancel = [(troop_set_slot, player, SLOT_OPACITY_PREVIEW, 0),
            (call_script, "script_" + SCRIPT_NAME),
            (presentation_set_duration, 0)]
  done = [(troop_get_slot, ":stored", player, SLOT_OPACITY_PREVIEW),
          (try_begin),
            (gt, ":stored", 0),
            (troop_set_slot, player, SLOT_OPACITY, ":stored"),
          (try_end),
          (troop_set_slot, player, SLOT_OPACITY_PREVIEW, 0),
          (presentation_set_duration, 0)]
  run = [(set_fixed_point_multiplier, 1000),
         (mouse_get_position, pos1),
         (position_get_x, ":mx", pos1),
         (position_get_y, ":my", pos1),
         (try_begin),
           (troop_slot_eq, player, SLOT_JUST_OPENED, 1),
           (troop_set_slot, player, SLOT_JUST_OPENED, 0),
         (else_try),
           (this_or_next|key_clicked, key_escape),
           (key_clicked, key_1)] + cancel + [
         (else_try),
           # the bar: press on it and drag
           (try_begin),
             (key_clicked, key_left_mouse_button),
             (is_between, ":mx", TRACK_X - 8, TRACK_X + TRACK_W + 9),
             (is_between, ":my", TRACK_Y - 10, TRACK_Y + TRACK_H + 11),
             (troop_set_slot, player, SLOT_OPACITY_DRAG, 1),
           (try_end),
           (try_begin),
             (troop_slot_eq, player, SLOT_OPACITY_DRAG, 1),
             (store_sub, ":value", ":mx", TRACK_X), (val_mul, ":value", 100), (val_div, ":value", TRACK_W),
             (val_clamp, ":value", 0, 101),
             (store_add, ":stored", ":value", 1),
             (neg|troop_slot_eq, player, SLOT_OPACITY_PREVIEW, ":stored"),     # only when it changes
             (troop_set_slot, player, SLOT_OPACITY_PREVIEW, ":stored")] + _opacity_view_ops(":value") + [
           (try_end),
           # let go: show it on the map, if the map does not show it yet
           (try_begin),
             (neg|key_is_down, key_left_mouse_button),
             (troop_slot_eq, player, SLOT_OPACITY_DRAG, 1),
             (troop_set_slot, player, SLOT_OPACITY_DRAG, 0),
             (troop_get_slot, ":stored", player, SLOT_OPACITY_PREVIEW),
             (gt, ":stored", 0),
             (neg|troop_slot_eq, player, SLOT_SHOWN_OPACITY, ":stored"),
             (troop_set_slot, player, SLOT_SHOWN_OPACITY, ":stored")] + _show_on_map(WINDOW_SETTINGS) + [
           (try_end)] + _button_run(SLOT_DONE, 25, 548, 40, done) + _button_run(SLOT_CANCEL, 95, 548, 50, cancel) + [
         (try_end)] + _release_ops()
  return (WINDOW_FLAGS, 0, [(ti_on_presentation_load, load),
                            (ti_on_presentation_run, run)])


# the kingdom colour picker, in the top right corner (screen units, 1000 x 750)
PICK_X0, PICK_X1, PICK_Y1 = 585, 990, 740            # panel; its bottom depends on the content
FIELD_X, FIELD_Y, FIELD_W, FIELD_H = 600, 525, 300, 165
FIELD_COLS, FIELD_ROWS = 50, 15                      # cells of 6 x 11 units
BAR_X, BAR_W = 915, 24
BAR_CELLS = 15


def _center_ranges(centers):
  """Runs of consecutive party ids among the centers, as (first, last + 1)."""
  ids = sorted(party for party, _, _ in centers)
  runs = []
  for party in ids:
    if runs and runs[-1][1] == party:
      runs[-1][1] = party + 1
    else:
      runs.append([party, party + 1])
  return runs


def _picker_refresh_ops(preview):
  """Shows the picker's hue, saturation and lightness: markers, lightness bar, swatch and RGB values.
  With preview, the colour is also put on the kingdom (and so on the map) until Done or Cancel."""
  player = "trp_player"
  ops = [(set_fixed_point_multiplier, 1000),
         (troop_get_slot, ":h", player, SLOT_HUE),
         (troop_get_slot, ":s", player, SLOT_SAT),
         (troop_get_slot, ":l", player, SLOT_LIGHT),
         # lightness bar in the current hue and saturation, light at the top
         (try_for_range, ":k", 0, BAR_CELLS),
           (store_mul, ":cell_l", ":k", 1000), (val_div, ":cell_l", BAR_CELLS - 1),
           (call_script, "script_" + HSL_SCRIPT, ":h", ":s", ":cell_l"),
           (store_add, ":slot", SLOT_BAR_CELLS, ":k"),
           (troop_get_slot, ":cell", player, ":slot"),
           (overlay_set_color, ":cell", reg0),
         (try_end),
         # markers: centred on the choice
         (store_mul, ":mx", ":h", FIELD_W), (val_div, ":mx", 360), (val_add, ":mx", FIELD_X - 4),
         (store_mul, ":my", ":s", FIELD_H), (val_div, ":my", 1000), (val_add, ":my", FIELD_Y - 4),
         (troop_get_slot, ":mark", player, SLOT_FIELD_MARK),
         (position_set_x, pos1, ":mx"), (position_set_y, pos1, ":my"), (overlay_set_position, ":mark", pos1),
         (val_add, ":mx", 2), (val_add, ":my", 2),
         (troop_get_slot, ":mark", player, SLOT_FIELD_MARK_IN),
         (position_set_x, pos1, ":mx"), (position_set_y, pos1, ":my"), (overlay_set_position, ":mark", pos1),
         (store_mul, ":my", ":l", FIELD_H), (val_div, ":my", 1000), (val_add, ":my", FIELD_Y - 2),
         (troop_get_slot, ":mark", player, SLOT_BAR_MARK),
         (position_set_x, pos1, BAR_X - 3), (position_set_y, pos1, ":my"), (overlay_set_position, ":mark", pos1),
         (val_add, ":my", 1),
         (troop_get_slot, ":mark", player, SLOT_BAR_MARK_IN),
         (position_set_x, pos1, BAR_X - 2), (position_set_y, pos1, ":my"), (overlay_set_position, ":mark", pos1),
         # the colour itself
         (call_script, "script_" + HSL_SCRIPT, ":h", ":s", ":l"),
         (assign, ":colour", reg0),
         (troop_get_slot, ":swatch", player, SLOT_SWATCH),
         (overlay_set_color, ":swatch", ":colour"),
         (store_div, reg1, ":colour", 0x10000),
         (store_div, reg2, ":colour", 0x100), (val_mod, reg2, 0x100),
         (store_mod, reg3, ":colour", 0x100),
         (troop_get_slot, ":text", player, SLOT_RGB_TEXT),
         (overlay_set_text, ":text", "@R {reg1}   G {reg2}   B {reg3}")]
  if preview:
    ops += [(store_add, ":stored", ":colour", 1),
            (troop_set_slot, player, SLOT_PREVIEW_COLOUR, ":stored"),
            (call_script, "script_" + SCRIPT_NAME)]
  return ops


def _rgb_to_hsl_ops(colour):
  """Hue, saturation and lightness of a 0xRRGGBB colour into the picker's slots."""
  player = "trp_player"
  ops = [(store_div, ":r", colour, 0x10000), (val_mod, ":r", 0x100),
         (store_div, ":g", colour, 0x100), (val_mod, ":g", 0x100),
         (store_mod, ":b", colour, 0x100)]
  for channel in (":r", ":g", ":b"):
    ops += [(val_mul, channel, 1000), (val_div, channel, 255)]
  ops += [(assign, ":max", ":r"), (val_max, ":max", ":g"), (val_max, ":max", ":b"),
          (assign, ":min", ":r"), (val_min, ":min", ":g"), (val_min, ":min", ":b"),
          (store_add, ":l", ":max", ":min"), (val_div, ":l", 2),
          (store_sub, ":d", ":max", ":min"),
          (assign, ":h", 0), (assign, ":s", 0),
          (try_begin),
            (gt, ":d", 0),
            (store_mul, ":den", ":l", 2), (val_sub, ":den", 1000), (val_abs, ":den"), (store_sub, ":den", 1000, ":den"),
            (val_max, ":den", 1),
            (store_mul, ":s", ":d", 1000), (val_div, ":s", ":den"), (val_clamp, ":s", 0, 1001),
            (try_begin),
              (eq, ":max", ":r"),
              (store_sub, ":h", ":g", ":b"), (val_mul, ":h", 60), (val_div, ":h", ":d"),
            (else_try),
              (eq, ":max", ":g"),
              (store_sub, ":h", ":b", ":r"), (val_mul, ":h", 60), (val_div, ":h", ":d"), (val_add, ":h", 120),
            (else_try),
              (store_sub, ":h", ":r", ":g"), (val_mul, ":h", 60), (val_div, ":h", ":d"), (val_add, ":h", 240),
            (try_end),
            (val_add, ":h", 360), (val_mod, ":h", 360),
          (try_end),
          (troop_set_slot, player, SLOT_HUE, ":h"),
          (troop_set_slot, player, SLOT_SAT, ":s"),
          (troop_set_slot, player, SLOT_LIGHT, ":l")]
  return ops


def picker_presentation(kingdom, centers):
  """The kingdom colour picker (Ctrl+2): a field of hues (across) and saturations (down), a lightness bar,
  a swatch with the RGB values, Done and Cancel. The colour goes on the kingdom at once and shows on the
  map when the mouse is let go (_show_on_map); Done keeps it (saved with the game), Cancel or Esc puts the
  old colour back. Without a kingdom of the player's own (a town, castle or village of
  fac_player_supporters_faction), a short note instead."""
  player = "trp_player"
  load = [(set_fixed_point_multiplier, 1000)] + _reopened_ops(WINDOW_PICKER) + [
          (troop_set_slot, player, SLOT_PICK_OPENED, 1),
          (troop_set_slot, player, SLOT_DRAG, 0),
          (troop_set_slot, player, SLOT_PICK_DONE, -1),
          (troop_set_slot, player, SLOT_PICK_CANCEL, -1),
          (troop_set_slot, player, SLOT_PRESSED, 0),
          (try_begin),
            (eq, ":reopened", 0),
            (troop_set_slot, player, SLOT_PREVIEW_COLOUR, 0),
            (troop_set_slot, player, SLOT_OLD_COLOUR, -1),
          (try_end),
          (assign, ":owned", 0)]
  if kingdom is not None:
    for first, end in _center_ranges(centers):
      load += [(try_for_range, ":center", _tag(tag_party, first), _tag(tag_party, end)),
                 (store_faction_of_party, ":faction", ":center"),
                 (eq, ":faction", kingdom),
                 (val_add, ":owned", 1),
               (try_end)]
  load += [(try_begin),
             (eq, ":owned", 0)]
  load += _panel(PICK_X0, 600, PICK_X1, PICK_Y1)
  load += _text(reg1, "@Your kingdom's colour", PICK_X0 + 15, PICK_Y1 - 28, 850, TITLE)
  load += _text(reg1, "@To choose your kingdom's colour you need a kingdom", PICK_X0 + 15, 680, 700, 0x000000)
  load += _text(reg1, "@of your own: hold a town, castle or village.", PICK_X0 + 15, 660, 700, 0x000000)
  load += _button_text(SLOT_PICK_CANCEL, "@Close", PICK_X0 + 15, 612)
  load += [(else_try)]
  load += _panel(PICK_X0, 422, PICK_X1, PICK_Y1)
  load += _text(reg1, "@Your kingdom's colour", PICK_X0 + 15, PICK_Y1 - 28, 850, TITLE)
  load += [(try_begin),
             (eq, ":reopened", 0),
             (faction_get_color, ":old", kingdom),
             (troop_set_slot, player, SLOT_OLD_COLOUR, ":old"),
             (store_add, ":shown", ":old", 1),
             (troop_set_slot, player, SLOT_SHOWN_COLOUR, ":shown")] + _rgb_to_hsl_ops(":old") + [
           (try_end)]
  # the field: hue across, saturation from grey (bottom) to full (top), at half lightness
  cw, ch, bh = FIELD_W // FIELD_COLS, FIELD_H // FIELD_ROWS, FIELD_H // BAR_CELLS
  load += _plane(reg1, FIELD_X - 1, FIELD_Y - 1, FIELD_W + 2, FIELD_H + 2, 0x000000)
  load += _plane(reg1, BAR_X - 1, FIELD_Y - 1, BAR_W + 2, FIELD_H + 2, 0x000000)
  load += [(try_for_range, ":j", 0, FIELD_ROWS),
             (store_mul, ":cell_s", ":j", 1000), (val_div, ":cell_s", FIELD_ROWS - 1),
             (store_mul, ":y", ":j", ch), (val_add, ":y", FIELD_Y),
             (try_for_range, ":i", 0, FIELD_COLS),
               (store_mul, ":cell_h", ":i", 360), (val_add, ":cell_h", 180), (val_div, ":cell_h", FIELD_COLS),
               (call_script, "script_" + HSL_SCRIPT, ":cell_h", ":cell_s", 500),
               (create_mesh_overlay, ":cell", "mesh_white_plane"),
               (overlay_set_color, ":cell", reg0),
               (store_mul, ":x", ":i", cw), (val_add, ":x", FIELD_X),
               (position_set_x, pos1, ":x"), (position_set_y, pos1, ":y"), (overlay_set_position, ":cell", pos1),
               (position_set_x, pos1, cw * 50 + 25), (position_set_y, pos1, ch * 50 + 25),
               (overlay_set_size, ":cell", pos1),
             (try_end),
           (try_end),
           # the lightness bar, dark at the bottom; refresh colours it
           (try_for_range, ":k", 0, BAR_CELLS),
             (create_mesh_overlay, ":cell", "mesh_white_plane"),
             (store_add, ":slot", SLOT_BAR_CELLS, ":k"),
             (troop_set_slot, player, ":slot", ":cell"),
             (store_mul, ":y", ":k", bh), (val_add, ":y", FIELD_Y),
             (position_set_x, pos1, BAR_X), (position_set_y, pos1, ":y"), (overlay_set_position, ":cell", pos1),
             (position_set_x, pos1, BAR_W * 50), (position_set_y, pos1, bh * 50 + 25),
             (overlay_set_size, ":cell", pos1),
           (try_end)]
  # markers (a black square with a white middle, a black line with a white middle), placed by refresh
  for slot, w, h, colour in ((SLOT_FIELD_MARK, 8, 8, 0x000000), (SLOT_FIELD_MARK_IN, 4, 4, 0xFFFFFF),
                             (SLOT_BAR_MARK, BAR_W + 6, 4, 0x000000), (SLOT_BAR_MARK_IN, BAR_W + 4, 2, 0xFFFFFF)):
    load += _plane(":mark", 0, 0, w, h, colour) + [(troop_set_slot, player, slot, ":mark")]
  # swatch, RGB values, hint, buttons
  load += _plane(reg1, FIELD_X - 1, 469, 62, 37, 0x000000)
  load += _plane(":swatch", FIELD_X, 470, 60, 35, 0xFFFFFF) + [(troop_set_slot, player, SLOT_SWATCH, ":swatch")]
  load += _text(":rgb", "@R {reg1}   G {reg2}   B {reg3}", FIELD_X + 75, 488, 700, 0x000000)
  load += [(troop_set_slot, player, SLOT_RGB_TEXT, ":rgb")]
  load += _text(reg1, "@Let go of the mouse to see it on the map. Done saves, Esc cancels.", FIELD_X, 430, 600, HINT)
  load += _button_text(SLOT_PICK_DONE, "@Done", 900, 486) + _button_text(SLOT_PICK_CANCEL, "@Cancel", 900, 458)
  load += _picker_refresh_ops(preview=False)
  load += [(try_end),
           (presentation_set_duration, 999999)]

  cancel = [(troop_set_slot, player, SLOT_PREVIEW_COLOUR, 0),
            (troop_get_slot, ":old", player, SLOT_OLD_COLOUR)]
  if kingdom is not None:
    cancel += [(try_begin),
                 (ge, ":old", 0),
                 (faction_set_color, kingdom, ":old"),
               (try_end)]
  cancel += [(call_script, "script_" + SCRIPT_NAME),
             (presentation_set_duration, 0)]
  done = [(troop_get_slot, ":want", player, SLOT_PREVIEW_COLOUR),
          (try_begin),
            (gt, ":want", 0),
            (troop_set_slot, player, SLOT_KINGDOM_COLOUR, ":want"),
          (try_end),
          (troop_set_slot, player, SLOT_PREVIEW_COLOUR, 0),
          (presentation_set_duration, 0)]

  # Esc or Ctrl+2 again cancels; clicks and drags in the field or the bar pick, letting go shows it
  run = [(set_fixed_point_multiplier, 1000),
         (mouse_get_position, pos1),
         (position_get_x, ":mx", pos1),
         (position_get_y, ":my", pos1),
         (try_begin),
           (troop_slot_eq, player, SLOT_PICK_OPENED, 1),
           (troop_set_slot, player, SLOT_PICK_OPENED, 0),
         (else_try),
           (this_or_next|key_clicked, key_escape),
           (key_clicked, key_2)] + cancel + [
         (else_try),
           (troop_slot_eq, player, SLOT_OLD_COLOUR, -1)] + _button_run(SLOT_PICK_CANCEL, PICK_X0 + 15, 612, 45, cancel) + [
         (else_try),
           (try_begin),
             (key_clicked, key_left_mouse_button),
             (is_between, ":mx", FIELD_X - 5, BAR_X + BAR_W + 6),
             (is_between, ":my", FIELD_Y - 5, FIELD_Y + FIELD_H + 6),
             (try_begin),
               (lt, ":mx", BAR_X - 4),
               (troop_set_slot, player, SLOT_DRAG, 1),
             (else_try),
               (troop_set_slot, player, SLOT_DRAG, 2),
             (try_end),
           (try_end),
           (troop_get_slot, ":drag", player, SLOT_DRAG),
           (try_begin),
             (gt, ":drag", 0),
             (key_is_down, key_left_mouse_button),
             (try_begin),
               (eq, ":drag", 1),
               (store_sub, ":h", ":mx", FIELD_X), (val_mul, ":h", 360), (val_div, ":h", FIELD_W),
               (val_clamp, ":h", 0, 360),
               (store_sub, ":s", ":my", FIELD_Y), (val_mul, ":s", 1000), (val_div, ":s", FIELD_H),
               (val_clamp, ":s", 0, 1001),
               (troop_set_slot, player, SLOT_HUE, ":h"),
               (troop_set_slot, player, SLOT_SAT, ":s"),
             (else_try),
               (store_sub, ":l", ":my", FIELD_Y), (val_mul, ":l", 1000), (val_div, ":l", FIELD_H),
               (val_clamp, ":l", 0, 1001),
               (troop_set_slot, player, SLOT_LIGHT, ":l"),
             (try_end)] + _picker_refresh_ops(preview=True) + [
           (else_try),
             (gt, ":drag", 0),
             (troop_set_slot, player, SLOT_DRAG, 0),
             (troop_get_slot, ":want", player, SLOT_PREVIEW_COLOUR),
             (gt, ":want", 0),
             (neg|troop_slot_eq, player, SLOT_SHOWN_COLOUR, ":want"),     # not shown on the map yet
             (troop_set_slot, player, SLOT_SHOWN_COLOUR, ":want")] + _show_on_map(WINDOW_PICKER) + [
           (try_end)] + _button_run(SLOT_PICK_DONE, 900, 486, 40, done) + _button_run(SLOT_PICK_CANCEL, 900, 458, 50, cancel) + [
         (try_end)] + _release_ops()
  return (WINDOW_FLAGS, 0, [(ti_on_presentation_load, load),
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

  script_ids = {SCRIPT_NAME: scripts.count, HSL_SCRIPT: scripts.count + 1}
  presentation_ids = {PRESENTATION_NAME: presentations.count, PICKER_NAME: presentations.count + 1}
  kingdom = _tag(tag_faction, mod.factions[PLAYER_KINGDOM]) if PLAYER_KINGDOM in mod.factions else None
  quick_ids = {}

  def resolve(symbol):
    if symbol.startswith("@"):
      if symbol not in quick_ids:
        quick_ids[symbol] = quick.append([quick_string_line(symbol[1:])])
      return _tag(tag_quick_string, quick_ids[symbol])
    if symbol.startswith("script_") and symbol[7:] in script_ids:
      return _tag(tag_script, script_ids[symbol[7:]])
    if symbol.startswith("prsnt_") and symbol[6:] in presentation_ids:
      return _tag(tag_presentation, presentation_ids[symbol[6:]])
    if symbol == "trp_player":
      return _tag(tag_troop, 0)
    if symbol.startswith("mesh_") and symbol in mod.meshes:
      return _tag(tag_mesh, mod.meshes[symbol])
    raise PatchError("Unknown identifier %s" % symbol)

  # shader parameter names first, in a fixed order
  for name in parameter_names(len(mod.centers), mod.num_factions):
    resolve("@" + name)
  scripts.append(["%s -1" % SCRIPT_NAME,
                  compile_block(update_script_ops(mod.centers, mod.num_factions, opacity, kingdom), resolve)])
  scripts.append(["%s -1" % HSL_SCRIPT, compile_block(hsl_script_ops(), resolve)])
  triggers.append(["%f %s" % (0.0, compile_block([(call_script, "script_" + SCRIPT_NAME)], resolve))])
  for name, (flags, mesh, presentation_triggers) in ((PRESENTATION_NAME, settings_presentation(opacity)),
                                                    (PICKER_NAME, picker_presentation(kingdom, mod.centers))):
    block = ["prsnt_%s %d %d %d" % (name, flags, mesh, len(presentation_triggers))]
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
