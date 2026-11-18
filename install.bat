@echo off
REM nupylab installer. Works on any supported Python, 3.7 (32-bit Windows 7)
REM included -- pip reads the environment markers in pyproject.toml and picks the
REM right dependency set by itself, so there is nothing to choose here.
REM
REM The pip/setuptools upgrade is required: the pip bundled with Python 3.7
REM predates PEP 621 and cannot read pyproject.toml's [project] table. PyPI
REM metadata makes that upgrade land on the newest version each interpreter
REM supports (pip 24.0 on 3.7), so this needs no version logic either.
REM
REM --only-binary :all: keeps pip from attempting any source build, which matters
REM on machines with no compiler.

setlocal

python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto failed

python -m pip install --only-binary :all: -e .
if errorlevel 1 goto failed

echo.
echo nupylab installed.
goto end

:failed
echo.
echo Install failed. See the pip output above.
exit /b 1

:end
endlocal
