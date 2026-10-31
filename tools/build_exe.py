"""Builds patcher/dist/CPRB-Map-Patcher.exe, the CPR&B Map Patcher window, with PyInstaller.

    python tools/build_exe.py [python of the build environment]

Run tools/build_shaders.py first so patcher/shaders/ is current. Use a PyInstaller whose bootloader was
compiled on this machine (see the README, "Building the patcher exe"): the prebuilt bootloader is shared by
so many programs, some of them malware, that several antivirus engines flag every exe made with it.
The build environment can also be given with the CPRB_BUILD_PYTHON environment variable.
"""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCHER = os.path.join(ROOT, "patcher")
sys.path.insert(0, PATCHER)
import cprb_patcher

NAME = "CPRB-Map-Patcher"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'champierr'),
      StringStruct('FileDescription', 'CPR&B Map Patcher'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 champierr. MIT License.'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'Colored Political Regions & Borders'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
  python = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CPRB_BUILD_PYTHON", sys.executable)
  build = os.path.join(PATCHER, "build")
  os.makedirs(build, exist_ok=True)
  version = cprb_patcher.VERSION
  nums = tuple(int(n) for n in version.split(".")) + (0,) * (4 - len(version.split(".")))
  version_file = os.path.join(build, "version_info.txt")
  with open(version_file, "w", encoding="utf-8") as f:
    f.write(VERSION_INFO.format(nums=nums, version=version, name=NAME))
  shaders, icon = os.path.join(PATCHER, "shaders"), os.path.join(PATCHER, "cprb.ico")
  command = [python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--noupx",
             "--name", NAME, "--icon", icon, "--version-file", version_file,
             "--add-data", shaders + os.pathsep + "shaders", "--add-data", icon + os.pathsep + ".",
             "--distpath", os.path.join(PATCHER, "dist"), "--workpath", build, "--specpath", build,
             os.path.join(PATCHER, "cprb_gui.py")]
  with open(os.path.join(PATCHER, "build_log.txt"), "w", encoding="utf-8") as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
  if result.returncode != 0:
    print("PyInstaller failed, see patcher/build_log.txt")
    sys.exit(result.returncode)
  print(os.path.join(PATCHER, "dist", NAME + ".exe"))


if __name__ == "__main__":
  main()
