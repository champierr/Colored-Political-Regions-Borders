"""Builds the store page images, the install tutorial video and the patcher icon from docs/screens/.

  docs/images/workshop_preview.jpg   1024x1024, under 1 MB (Steam Workshop preview)
  docs/images/cover.jpg              1920x1080 (Nexus main image, GitHub README)
  docs/images/gallery_*.jpg          1920x1080 screenshots with a caption
  docs/video/cprb_install_tutorial.mp4
  patcher/cprb.ico
"""
import os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(ROOT, "docs", "screens")
IMAGES = os.path.join(ROOT, "docs", "images")
VIDEO = os.path.join(ROOT, "docs", "video")
FONTS = r"C:\Windows\Fonts"

TITLE = "Colored Political Regions & Borders"
REPO = "github.com/champierr/Colored-Political-Regions-Borders"
W, H = 1920, 1080
GOLD = (233, 196, 106)
PARCHMENT = (244, 232, 204)
INK = (20, 17, 14)


def font(name, size):
  return ImageFont.truetype(os.path.join(FONTS, name), size)


def title_font(size): return font("BOOKOSB.TTF", size)
def body_font(size): return font("georgia.ttf", size)
def body_bold(size): return font("georgiab.ttf", size)


def screen(name, crop=None):
  img = Image.open(os.path.join(SCREENS, name)).convert("RGB")
  return img.crop(crop) if crop else img


def cover_fit(img, size):
  """Scale and center-crop img to fill size."""
  w, h = size
  scale = max(w / img.width, h / img.height)
  img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
  left, top = (img.width - w) // 2, (img.height - h) // 2
  return img.crop((left, top, left + w, top + h))


def shade(img, box, alpha=170, radius=0):
  """Darken a box of img for text on top."""
  overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
  ImageDraw.Draw(overlay).rounded_rectangle(box, radius=radius, fill=(0, 0, 0, alpha))
  return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def gradient_band(img, top, bottom, strength=200, from_top=False):
  """Vertical fade to black between top and bottom (for titles over screenshots)."""
  band = Image.new("L", (1, bottom - top))
  for y in range(bottom - top):
    t = y / max(1, bottom - top - 1)
    band.putpixel((0, y), int(strength * ((1 - t) if from_top else t)))
  mask = Image.new("L", img.size, 0)
  mask.paste(band.resize((img.width, bottom - top)), (0, top))
  black = Image.new("RGB", img.size, (0, 0, 0))
  return Image.composite(black, img, mask)


def text_center(draw, y, text, fnt, fill, width, shadow=True):
  w = draw.textlength(text, font=fnt)
  x = (width - w) / 2
  if shadow:
    draw.text((x + 3, y + 3), text, font=fnt, fill=(0, 0, 0))
  draw.text((x, y), text, font=fnt, fill=fill)


# Screens used everywhere (crops remove the fps counter, "Pause" text and the bottom bar)
def native_overview(): return screen("native_overview.webp")
def native_zoom(): return screen("native_border_zoom.webp")
def cj_anatolia(): return screen("cj_anatolia.jpg", (0, 20, 1920, 930))
def cj_panel(): return screen("cj_settings_panel.jpg", (0, 14, 1920, 930))
def native_picker(): return screen("native_kingdom_colour.jpg", (258, 0, 1920, 935))   # 16:9, the whole panel
def picker_before(): return screen("native_kingdom_colour_before.jpg", (934, 0, 1905, 930))
def picker_after(): return screen("native_kingdom_colour.jpg", (934, 0, 1905, 930))
def native_kingdom_map(): return screen("native_kingdom_colour_map.jpg", (0, 20, 1920, 930))


# --------------------------------------------------------------------------------------------- images
def make_cover():
  img = cover_fit(native_overview(), (W, H))
  img = gradient_band(img, 0, 420, 230, from_top=True)
  img = gradient_band(img, 760, H, 230)
  d = ImageDraw.Draw(img)
  text_center(d, 70, TITLE, title_font(92), GOLD, W)
  text_center(d, 190, "A political map for Mount & Blade: Warband", body_font(46), PARCHMENT, W)
  text_center(d, 880, "Native  \u2022  works with other mods through the CPR&B Map Patcher", body_font(40), PARCHMENT, W)
  text_center(d, 950, "Zoom out: country colours   \u2022   Zoom in: terrain with coloured borders   \u2022   Ctrl+1: opacity",
              body_font(30), (210, 200, 180), W)
  img.save(os.path.join(IMAGES, "cover.jpg"), quality=90)


def make_workshop_preview():
  size = 1024
  img = cover_fit(native_overview(), (size, size))
  img = gradient_band(img, 0, 360, 235, from_top=True)
  img = gradient_band(img, 720, size, 220)
  d = ImageDraw.Draw(img)
  text_center(d, 60, "Colored Political", title_font(96), GOLD, size)
  text_center(d, 165, "Regions & Borders", title_font(96), GOLD, size)
  text_center(d, 900, "Political map for Warband", body_font(44), PARCHMENT, size)
  path = os.path.join(IMAGES, "workshop_preview.jpg")
  for quality in (90, 85, 80, 75):
    img.save(path, quality=quality)
    if os.path.getsize(path) < 1000 * 1000:
      break


def make_comparison():
  img = Image.new("RGB", (W, H), INK)
  left = cover_fit(native_overview(), (940, 900))
  right = cover_fit(native_zoom(), (940, 900))
  img.paste(left, (13, 160))
  img.paste(right, (967, 160))
  d = ImageDraw.Draw(img)
  text_center(d, 30, "Zoom out: political map   \u2022   Zoom in: physical map, borders stay", title_font(52), GOLD, W)
  d.text((40, 1070 - 50), "Native", font=body_font(32), fill=PARCHMENT)
  img.save(os.path.join(IMAGES, "gallery_zoom_levels.jpg"), quality=90)


def make_kingdom_colour_compare():
  img = Image.new("RGB", (W, H), INK)
  img.paste(cover_fit(picker_before(), (940, 900)), (13, 160))
  img.paste(cover_fit(picker_after(), (940, 900)), (967, 160))
  d = ImageDraw.Draw(img)
  text_center(d, 30, "Ctrl+2: your kingdom in a colour of your own", title_font(52), GOLD, W)
  d.text((40, 1070 - 50), "Before: the default red", font=body_font(32), fill=PARCHMENT)
  d.text((994, 1070 - 50), "After: picked in game", font=body_font(32), fill=PARCHMENT)
  img.save(os.path.join(IMAGES, "gallery_kingdom_colour_compare.jpg"), quality=90)


def make_gallery(name, img, caption):
  img = cover_fit(img, (W, H))
  img = gradient_band(img, 860, H, 235)
  d = ImageDraw.Draw(img)
  text_center(d, 960, caption, body_bold(44), PARCHMENT, W)
  img.save(os.path.join(IMAGES, "gallery_%s.jpg" % name), quality=90)


def make_images():
  os.makedirs(IMAGES, exist_ok=True)
  make_cover()
  make_workshop_preview()
  make_comparison()
  make_gallery("native", native_overview(), "Native: every town, castle and village region in its owner's colour")
  make_gallery("border_glow", native_zoom(), "Zoomed in: the terrain stays, borders keep a glow of each side's colour")
  make_gallery("crusade_against_jihad", cj_anatolia(), "Crusade Against Jihad: 269 regions, 68 factions, the mod's own colours")
  make_gallery("settings_panel", cj_panel(), "Ctrl+1: map colour opacity, saved with your game")
  make_gallery("kingdom_colour", native_picker(), "Ctrl+2: pick your kingdom's colour, let go and the map shows it")
  make_kingdom_colour_compare()
  make_gallery("kingdom_colour_map", native_kingdom_map(), "Zoomed out: your whole realm in the colour you picked")


# ---------------------------------------------------------------------------------------------- icon
def make_icon():
  size = 256
  img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
  d = ImageDraw.Draw(img)
  d.ellipse((8, 8, size - 8, size - 8), fill=(32, 28, 24, 255))
  regions = [((128, 128), [(128, 20), (236, 110), (128, 128)], (204, 64, 52)),
             ((128, 128), [(236, 110), (200, 230), (128, 128)], (64, 170, 160)),
             ((128, 128), [(200, 230), (40, 210), (128, 128)], (222, 190, 70)),
             ((128, 128), [(40, 210), (20, 90), (128, 128)], (140, 90, 190)),
             ((128, 128), [(20, 90), (128, 20), (128, 128)], (70, 130, 60))]
  mask = Image.new("L", (size, size), 0)
  ImageDraw.Draw(mask).ellipse((20, 20, size - 20, size - 20), fill=255)
  layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
  ld = ImageDraw.Draw(layer)
  for _, poly, colour in regions:
    ld.polygon([(128, 128)] + poly[:2], fill=colour + (255,))
  for _, poly, _ in regions:
    ld.line([(128, 128), poly[0]], fill=(15, 12, 10, 255), width=9)
  img.paste(layer, (0, 0), mask)
  d.ellipse((8, 8, size - 8, size - 8), outline=GOLD + (255,), width=10)
  img.save(os.path.join(ROOT, "patcher", "cprb.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# --------------------------------------------------------------------------------------------- video
FPS = 30


def caption_frame(base, step, en, tr):
  """Bottom caption with English and Turkish lines, optional step badge."""
  img = base.copy()
  img = shade(img, (0, H - 190, W, H), 200)
  d = ImageDraw.Draw(img)
  text_center(d, H - 170, en, body_bold(46), (255, 255, 255), W, shadow=False)
  text_center(d, H - 100, tr, body_font(38), (215, 205, 185), W, shadow=False)
  if step:
    d.rounded_rectangle((40, 36, 250, 100), radius=14, fill=(0, 0, 0))
    d.text((62, 44), step, font=body_bold(40), fill=GOLD)
  return img


def gui_slide(name, highlight):
  """Patcher screenshot centered on a dark background with a gold box around highlight."""
  gui = Image.open(os.path.join(IMAGES, name)).convert("RGB")
  scale = 1.45
  gui = gui.resize((round(gui.width * scale), round(gui.height * scale)), Image.LANCZOS)
  img = Image.new("RGB", (W, H), (26, 23, 20))
  x0, y0 = (W - gui.width) // 2, 40
  img.paste(gui, (x0, y0))
  if highlight:
    x1, y1, x2, y2 = (round(v * scale) for v in highlight)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0 + x1 - 10, y0 + y1 - 10, x0 + x2 + 10, y0 + y2 + 10), radius=10, outline=GOLD, width=7)
  return img


def card(lines):
  """Plain dark card with centered lines [(text, font, colour)]."""
  img = cover_fit(native_overview(), (W, H)).filter(ImageFilter.GaussianBlur(14))
  img = shade(img, (0, 0, W, H), 175)
  d = ImageDraw.Draw(img)
  total = sum(f.size + 30 for _, f, _ in lines)
  y = (H - 190 - total) // 2
  for text, fnt, colour in lines:
    text_center(d, y, text, fnt, colour, W)
    y += fnt.size + 30
  return img


def scenes():
  """(image, seconds, step, English caption, Turkish caption, slow zoom)"""
  s = []
  s.append((card([(TITLE, title_font(84), GOLD),
                  ("Installing into any Warband mod", body_font(52), PARCHMENT),
                  ("Herhangi bir Warband moduna kurulum", body_font(44), (215, 205, 185))]),
            5, None, "CPR&B Map Patcher", "CPR&B Map Patcher", True))
  s.append((card([("1. Download", title_font(72), GOLD),
                  ("CPRB-Map-Patcher.exe", body_bold(60), PARCHMENT),
                  (REPO + "/releases", body_font(40), (215, 205, 185))]),
            6, "1 / 7", "Download the patcher from GitHub Releases (or Nexus Mods)",
            "Patcher'\u0131 GitHub Releases'tan (veya Nexus Mods'tan) indir", False))
  s.append((card([("2. Run it", title_font(72), GOLD),
                  ("If Windows SmartScreen warns:", body_font(48), PARCHMENT),
                  ("More info  >  Run anyway", body_bold(56), PARCHMENT)]),
            6, "2 / 7", "The app is not code-signed, so Windows may ask once",
            "Uygulama imzal\u0131 de\u011fil; Windows uyar\u0131rsa: Ek bilgi > Yine de \u00e7al\u0131\u015ft\u0131r", False))
  s.append((gui_slide("patcher_before.png", (10, 70, 646, 92)), 7, "3 / 7",
            "Pick your mod. Steam and Workshop mods are found automatically",
            "Modunu se\u00e7. Steam ve Workshop modlar\u0131 otomatik bulunur (yoksa Browse...)", False))
  s.append((gui_slide("patcher_before.png", (8, 494, 480, 516)), 6, "4 / 7",
            "Choose the starting map colour opacity for new games",
            "Yeni oyunlar i\u00e7in ba\u015flang\u0131\u00e7 renk opakl\u0131\u011f\u0131n\u0131 se\u00e7", False))
  s.append((gui_slide("patcher_after.png", (8, 525, 102, 551)), 7, "5 / 7",
            "Press Install / Update. Original files are backed up",
            "Install / Update'e bas. Orijinal dosyalar yedeklenir", False))
  s.append((cover_fit(cj_anatolia(), (W, H)), 7, "6 / 7",
            "Start Warband with that mod. A new game is recommended",
            "Warband'\u0131 o modla ba\u015flat. Yeni oyun ba\u015flatman \u00f6nerilir", True))
  s.append((cover_fit(cj_panel(), (W, H)), 7, "7 / 7",
            "On the world map press Ctrl+1 to change the colour opacity",
            "D\u00fcnya haritas\u0131nda Ctrl+1 ile renk opakl\u0131\u011f\u0131n\u0131 ayarla", False))
  s.append((card([("Uninstall / Updates", title_font(66), GOLD),
                  ("Same app > Uninstall removes exactly what was added", body_font(46), PARCHMENT),
                  ("A Steam Workshop update of the mod removes the patch:", body_font(46), PARCHMENT),
                  ("just run the patcher again", body_bold(46), PARCHMENT)]),
            7, None, "Ayn\u0131 uygulama > Uninstall ile tamamen kald\u0131r\u0131l\u0131r",
            "Workshop modu g\u00fcncellenirse yamay\u0131 tekrar uygula", False))
  s.append((card([("Tested & stable", title_font(66), GOLD),
                  ("Native  \u2022  Crusade Against Jihad", body_bold(54), PARCHMENT),
                  ("Other mods are not tested yet \u2014 feedback welcome!", body_font(44), PARCHMENT),
                  (REPO, body_font(40), (215, 205, 185))]),
            7, None, "Stable on Native and Crusade Against Jihad. Other mods: untested",
            "Native ve Crusade Against Jihad'da stabil. Di\u011fer modlar hen\u00fcz test edilmedi", False))
  return s


def make_video():
  import imageio.v2 as imageio
  os.makedirs(VIDEO, exist_ok=True)
  path = os.path.join(VIDEO, "cprb_install_tutorial.mp4")
  writer = imageio.get_writer(path, fps=FPS, codec="libx264", quality=8, pixelformat="yuv420p",
                              macro_block_size=8, ffmpeg_log_level="error")
  fade = int(0.5 * FPS)
  previous = None
  for img, seconds, step, en, tr, zoom in scenes():
    frames = int(seconds * FPS)
    still = caption_frame(img, step, en, tr) if not zoom else None
    for i in range(frames):
      if zoom:
        z = 1.0 + 0.06 * i / frames               # slow push in
        cw, ch = round(W / z), round(H / z)
        frame = img.crop(((W - cw) // 2, (H - ch) // 2, (W - cw) // 2 + cw, (H - ch) // 2 + ch)).resize((W, H))
        frame = caption_frame(frame, step, en, tr)
      else:
        frame = still
      if previous is not None and i < fade:
        frame = Image.blend(previous, frame, i / fade)
      writer.append_data(__import__("numpy").asarray(frame))
    previous = frame
  writer.close()
  print("wrote", path, "%.1f MB" % (os.path.getsize(path) / 1e6))


if __name__ == "__main__":
  what = sys.argv[1:] or ["images", "icon", "video"]
  if "images" in what:
    make_images()
  if "icon" in what:
    make_icon()
  if "video" in what:
    make_video()
