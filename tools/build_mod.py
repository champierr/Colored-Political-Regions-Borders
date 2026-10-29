"""Builds the Colored Political Regions & Borders module: Native from src/, then the CPR&B Map Patcher
applied to it, exactly as players apply it to other mods.

Run tools/build_shaders.py first whenever shaders/ changed.
"""
import os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "patcher"))
from module_info import export_dir
import cprb_patcher


def main():
  r = subprocess.run([sys.executable, os.path.join(ROOT, "src", "build.py")])
  if r.returncode != 0:
    sys.exit(r.returncode)
  # the module system rewrote the text files, so an older patch record no longer applies
  shutil.rmtree(os.path.join(export_dir, cprb_patcher.BACKUP_DIR), ignore_errors=True)
  for name in cprb_patcher.SHADER_FILES:
    if os.path.isfile(os.path.join(export_dir, name)):
      os.remove(os.path.join(export_dir, name))
  try:
    cprb_patcher.install(export_dir)
  except cprb_patcher.PatchError as e:
    print("ERROR: %s" % e)
    sys.exit(1)


if __name__ == "__main__":
  main()
