"""Packs the release files of the current version (cprb_patcher.VERSION).

    python tools/package_release.py [Native module folder]

  release/v<version>/  CPRB-Map-Patcher.exe, Colored_Political_Regions_and_Borders_Native_v<version>.zip and
                       cprb_install_tutorial.mp4, for the GitHub release
  release/nexus/       the patcher, the Native module and the video as zips with their install guides inside,
                       and the store images
Build first: build_all.bat (Native with the patcher applied, into the module folder) and tools/build_exe.py.
"""
import os, shutil, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "patcher"))
sys.path.insert(0, os.path.join(ROOT, "src"))
import cprb_patcher

MODULE_TOP = "Colored Political Regions & Borders"
IMAGES = ["cover.jpg", "gallery_zoom_levels.jpg", "gallery_native.jpg", "gallery_border_glow.jpg",
          "gallery_crusade_against_jihad.jpg", "gallery_kingdom_colour.jpg", "gallery_kingdom_colour_compare.jpg",
          "gallery_kingdom_colour_map.jpg", "patcher_after.png"]


def zip_module(path, module, guide=None):
  """The module folder (without CPRB_backup, which only the patcher needs) as <MODULE_TOP>/..."""
  with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    if guide:
      z.write(guide, "HOW_TO_INSTALL.txt")
    for folder, dirs, files in os.walk(module):
      dirs[:] = [d for d in dirs if d != cprb_patcher.BACKUP_DIR]
      for name in files:
        full = os.path.join(folder, name)
        z.write(full, os.path.join(MODULE_TOP, os.path.relpath(full, module)))


def main():
  if len(sys.argv) > 1:
    module = sys.argv[1]
  else:
    from module_info import export_dir
    module = export_dir
  version = cprb_patcher.VERSION
  installed = cprb_patcher.ModInfo(module).installed
  if not installed or installed["version"] != version:
    sys.exit("The module in %s does not have CPR&B %s installed; run build_all.bat first." % (module, version))
  exe = os.path.join(ROOT, "patcher", "dist", "CPRB-Map-Patcher.exe")
  video = os.path.join(ROOT, "docs", "video", "cprb_install_tutorial.mp4")
  store, images = os.path.join(ROOT, "docs", "store"), os.path.join(ROOT, "docs", "images")
  native = "Colored_Political_Regions_and_Borders_Native_v%s.zip" % version

  out = os.path.join(ROOT, "release", "v" + version)
  os.makedirs(out, exist_ok=True)
  shutil.copy2(exe, out)
  shutil.copy2(video, out)
  zip_module(os.path.join(out, native), module)

  nexus = os.path.join(ROOT, "release", "nexus")
  if os.path.isdir(nexus):
    shutil.rmtree(nexus)
  os.makedirs(os.path.join(nexus, "images"))
  with zipfile.ZipFile(os.path.join(nexus, "CPRB-Map-Patcher_v%s.zip" % version), "w", zipfile.ZIP_DEFLATED,
                       compresslevel=9) as z:
    z.write(exe, "CPRB-Map-Patcher.exe")
    z.write(os.path.join(store, "HOW_TO_INSTALL.txt"), "HOW_TO_INSTALL.txt")
  zip_module(os.path.join(nexus, native), module, os.path.join(store, "HOW_TO_INSTALL_NATIVE.txt"))
  with zipfile.ZipFile(os.path.join(nexus, "CPRB_Install_Video_v%s.zip" % version), "w", zipfile.ZIP_STORED) as z:
    z.write(video, "cprb_install_tutorial.mp4")
  for name in IMAGES:
    shutil.copy2(os.path.join(images, name), os.path.join(nexus, "images", name))
  for folder in (out, nexus):
    for name in sorted(os.listdir(folder)):
      path = os.path.join(folder, name)
      if os.path.isfile(path):
        print("%-70s %6.1f MB" % (os.path.relpath(path, ROOT), os.path.getsize(path) / 1e6))


if __name__ == "__main__":
  main()
