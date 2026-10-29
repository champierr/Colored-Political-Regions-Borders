@echo off
rem Colored Political Regions and Borders: builds Native and applies the CPR&B Map Patcher to it.
rem After changing anything in shaders\ run first:  python tools\build_shaders.py
cd /d "%~dp0"
python tools\build_mod.py || goto :err
echo TAMAM.
pause
exit /b 0
:err
echo HATA olustu.
pause
exit /b 1
