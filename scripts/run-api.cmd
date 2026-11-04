@echo off
set "ANVIL_EXECUTOR=deepagent"
set "PYTHONUNBUFFERED=1"
if not "%~3"=="" set "ANVIL_DATABASE_URL=%~3"
if not "%~4"=="" set "ANVIL_KB_DATABASE_URL=%~4"
cd /d "%~dp0..\backend"
".venv\Scripts\python.exe" -u -c "import os, dotenv, uvicorn; dotenv.load_dotenv('../.env'); dotenv.load_dotenv('.env'); [os.environ.setdefault('JIRA_URL' if k == 'SITE' else f'JIRA_{k}', os.environ[f'ATLASSIAN_{k}']) for k in ('SITE', 'EMAIL', 'API_TOKEN') if f'ATLASSIAN_{k}' in os.environ]; uvicorn.run('anvil.api.app:app', host='%~1', port=%~2, loop='anvil.api.serve:api_loop_factory')" > "%~dp0..\api-8000.log" 2> "%~dp0..\api-8000.err.log"
