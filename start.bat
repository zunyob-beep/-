@echo off
rem 한 번에 실행하기 (윈도우)
rem
rem   start.bat           이 컴퓨터에서만 열기
rem   start.bat --lan     같은 와이파이의 아이패드·폰에서도 열기
rem
rem 처음 실행하면 파이썬 환경을 만들고 업비트 시세를 받습니다.
rem 두 번째부터는 받아둔 것에 새 봉만 이어 붙이므로 금방 뜹니다.

setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   파이썬 3.10 이상이 필요합니다.
  echo   https://www.python.org/downloads/ 에서 내려받아 설치하세요.
  echo   설치할 때 "Add Python to PATH"를 꼭 체크하세요.
  echo.
  pause
  exit /b 1
)

if not exist .venv (
  echo   처음 실행이라 준비를 합니다. 1~2분 걸립니다.
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat

python -c "import patternscan" >nul 2>&1
if errorlevel 1 (
  echo   필요한 것을 설치하는 중...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e .
)

if not defined MARKET set "MARKET=KRW-BTC"
if not defined COUNT set "COUNT=129600"

if not exist "data\%MARKET%_minute1.csv" (
  echo.
  echo   업비트에서 시세를 받습니다. 처음 한 번만 오래 걸립니다.
  echo   중간에 끊겨도 받은 만큼 저장되니, 다시 실행하면 이어서 받습니다.
  echo.
)
rem 시세를 못 받아도 화면은 연다. 받아둔 것으로도 돌아가고,
rem 아무것도 없으면 화면이 무엇을 하면 되는지 안내한다.
python -m patternscan fetch --market "%MARKET%" --count %COUNT%
if errorlevel 1 (
  echo.
  echo   시세를 받지 못했습니다. 그래도 화면은 엽니다.
  echo   어디가 막혔는지 보려면 다른 창에서: python -m patternscan doctor
  echo.
)

set "HOSTARG="
if "%~1"=="--lan" set "HOSTARG=--host 0.0.0.0"

echo.
echo   화면을 엽니다. 끝내려면 이 창에서 Ctrl+C 를 누르세요.
echo   브라우저 주소창의 설치 아이콘을 누르면 앱처럼 쓸 수 있습니다.
echo.
python -m patternscan ui --market "%MARKET%" %HOSTARG%
pause
