# Reproducción offline desde un entorno limpio (M12, 13.3).
#
# Clona el commit actual en un directorio temporal (sin .venv, node_modules ni .env), construye
# la imagen fijada por digest, levanta sólo db + migrate + api en un proyecto Compose propio
# (`evallab-repro`, puertos 56432/58000) y, dentro del contenedor de la API:
#   1. publica las cuatro suites y comprueba que sus hashes coinciden con los locks versionados;
#   2. ejecuta `agentic-benchmark-v1` con los dos agentes scripted (5 repeticiones);
#   3. exporta una traza por la API y la verifica con `evallab-verify-trace`.
# Ningún paso llama a modelos ni abre red externa en tiempo de ejecución (la construcción de la
# imagen sí descarga dependencias fijadas por hash). Al final borra sólo su propio proyecto.
#
#   pwsh scripts/reproduce-offline.ps1 -Out results/m12-v1-offline-repro

param(
    [string]$Out = "results/m12-v1-offline-repro",
    [int]$Repetitions = 5,
    [string]$Project = "evallab-repro",
    [int]$DbPort = 56432,
    [int]$ApiPort = 58000
)

$ErrorActionPreference = "Stop"
$repo = (git rev-parse --show-toplevel).Trim()
$commit = (git -C $repo rev-parse HEAD).Trim()
$dirty = [bool](git -C $repo status --porcelain)
$work = Join-Path ([System.IO.Path]::GetTempPath()) "evallab-repro-$($commit.Substring(0, 12))"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
git clone --quiet --no-hardlinks $repo $work
git -C $work checkout --quiet $commit

$password = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
$envFile = @(
    "POSTGRES_DB=evallab",
    "POSTGRES_USER=evallab",
    "POSTGRES_PASSWORD=$password",
    "POSTGRES_PORT=$DbPort",
    "API_PORT=$ApiPort",
    "EVALLAB_READ_ONLY=0"
) -join "`n"
[System.IO.File]::WriteAllText((Join-Path $work ".env"), "$envFile`n")

$outDir = Join-Path $repo $Out
New-Item -ItemType Directory -Force $outDir | Out-Null
$compose = @("compose", "--project-directory", $work, "-p", $Project)
$steps = [ordered]@{}
try {
    docker @compose up --build --detach --wait db migrate api | Out-Null
    $image = (docker image inspect evallab-backend:local --format '{{.Id}}').Trim()

    foreach ($suite in "pilot", "pilot-models", "retrieval-v1", "v1") {
        docker @compose exec -T api evallab-benchmark publish $suite | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "lock de $suite no coincide" }
        $steps["lock:$suite"] = "matches committed lock"
    }

    docker @compose exec -T api evallab-benchmark run v1 --repetitions $Repetitions --out /tmp/v1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "run v1 falló" }
    # /tmp es tmpfs (docker cp no lo ve): los ficheros se leen con cat.
    foreach ($name in "report.json", "report.md", "lock.json", "manifest.json", "run.json") {
        $content = docker @compose exec -T api cat "/tmp/v1/$name"
        [System.IO.File]::WriteAllText((Join-Path $outDir $name), (($content -join "`n") + "`n"))
    }
    $steps["run:v1"] = "$Repetitions repetitions"

    $report = Get-Content (Join-Path $outDir "report.json") -Raw | ConvertFrom-Json
    $experiment = $report.experiment.id
    $runs = Invoke-RestMethod "http://127.0.0.1:$ApiPort/experiments/$experiment/runs"
    $run = $runs[0].id
    $exportPath = Join-Path $outDir "sample-trace.jsonl"
    $manifestPath = Join-Path $outDir "sample-trace-manifest.json"
    Invoke-WebRequest "http://127.0.0.1:$ApiPort/runs/$run/trace/export" -OutFile $exportPath | Out-Null
    Invoke-WebRequest "http://127.0.0.1:$ApiPort/runs/$run/trace/manifest" -OutFile $manifestPath | Out-Null
    # Dentro del contenedor (bytes intactos, sin pasar por tuberías de PowerShell).
    $fetch = "import urllib.request as u; b='http://127.0.0.1:8000/runs/$run/trace/'; " +
        "open('/tmp/e.jsonl','wb').write(u.urlopen(b+'export').read()); " +
        "open('/tmp/m.json','wb').write(u.urlopen(b+'manifest').read())"
    docker @compose exec -T api python -c $fetch
    docker @compose exec -T api evallab-verify-trace /tmp/m.json /tmp/e.jsonl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "la traza exportada no verifica" }
    $steps["trace:verify"] = "run $run verified"

    $summary = [ordered]@{
        commit = $commit
        host_worktree_dirty_at_run = $dirty
        image_id = $image
        compose_project = $Project
        repetitions = $Repetitions
        steps = $steps
        finished_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $outDir "repro.json"),
        (($summary | ConvertTo-Json -Depth 5) -replace "`r`n", "`n") + "`n"
    )
}
finally {
    docker @compose down --volumes --remove-orphans | Out-Null
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
