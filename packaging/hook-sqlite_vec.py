# PyInstaller hook для sqlite-vec (спайк A в собранных пакетах, P1-03).
#
# Без него бандл импортирует Python-часть sqlite_vec, но load_extension()
# не находит vec0.dll → «Не найден указанный модуль» и честная деградация до
# exact-fallback. Hook кладёт динамические библиотеки в каталог пакета.
from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("sqlite_vec")
