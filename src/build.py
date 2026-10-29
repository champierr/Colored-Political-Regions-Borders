"""Python 3 replacement for build_module.bat: runs every process_*.py step in order."""
import subprocess, sys, re, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
steps = re.findall(r"^python (\S+)", open("build_module.bat").read(), re.M)
for step in steps:
  r = subprocess.run([sys.executable, step], capture_output=True, text=True)
  out = r.stdout + r.stderr
  if r.returncode != 0 or "error" in out.lower():
    print("== %s\n%s" % (step, out.strip()))
    if r.returncode != 0: sys.exit(1)
print("Build finished.")
