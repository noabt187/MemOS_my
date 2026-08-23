@echo off
setlocal

where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Install uv first, then run this script again.
  exit /b 1
)

pushd "%~dp0"
uv run --frozen --extra skill-mem python "%~dp0scripts\memos_chat.py" %*
set "MEMOS_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %MEMOS_EXIT_CODE%
