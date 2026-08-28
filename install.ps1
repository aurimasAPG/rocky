# Rocky installer — Windows PowerShell.
#
#   irm https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.ps1 | iex
#
# Installs uv if missing, then Rocky as an isolated uv tool from Git.
# Knobs: $env:ROCKY_REPO, $env:ROCKY_REF, $env:ROCKY_SOURCE, $env:ROCKY_EXTRAS, $env:ROCKY_PYTHON

$ErrorActionPreference = "Stop"

$Repo   = if ($env:ROCKY_REPO)   { $env:ROCKY_REPO }   else { "https://github.com/aurimasAPG/rocky" }
$Ref    = if ($env:ROCKY_REF)    { $env:ROCKY_REF }    else { "main" }
$Py     = if ($env:ROCKY_PYTHON) { $env:ROCKY_PYTHON } else { "3.12" }
$Extras = $env:ROCKY_EXTRAS

function Say($msg) { Write-Host "rocky " -ForegroundColor DarkYellow -NoNewline; Write-Host $msg }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "installing uv…"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv did not install; see https://docs.astral.sh/uv/" }

if ($env:ROCKY_SOURCE) {
    $Spec = $env:ROCKY_SOURCE
    Say "installing from $Spec"
} else {
    $Spec = "git+$Repo@$Ref"
    Say "installing from $Repo ($Ref)"
}
if ($Extras) { $Spec = "rocky[$Extras] @ $Spec" }

uv tool install --force --reinstall --python $Py $Spec | Out-Null
uv tool update-shell | Out-Null

$rocky = Join-Path $env:USERPROFILE ".local\bin\rocky.exe"
if (Test-Path $rocky) { Say (& $rocky --version) } else { Say "installed" }

if ($Extras -eq "browser") {
    Say "installing Chromium for the browser tools…"
    uv tool run --from playwright playwright install chromium | Out-Null
}

Write-Host ""
Write-Host "  Next:"
Write-Host "    1. put your key in %USERPROFILE%\.env    OPENROUTER_API_KEY=sk-or-..."
Write-Host "    2. cd into a project folder and run:    rocky"
Write-Host "    3. guide:                               rocky help"
Write-Host "  Open a new terminal if 'rocky' is not found yet."
Write-Host ""
