#requires -Version 7.4
[CmdletBinding()]
param([string]$PostgresBin = $env:PGBIN)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not [OperatingSystem]::IsWindows()) { throw 'This smoke test requires Windows.' }

$repositoryRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PostgresBin)) {
    $installations = Get-ChildItem -LiteralPath (Join-Path $env:ProgramFiles 'PostgreSQL') -Directory |
        Where-Object { $_.Name -match '^\d+(\.\d+)*$' } |
        Sort-Object { [version]($_.Name -replace '^(\d+)$', '$1.0') } -Descending
    $PostgresBin = $installations | ForEach-Object { Join-Path $_.FullName 'bin' } |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ 'initdb.exe') } | Select-Object -First 1
}
foreach ($tool in @('initdb.exe', 'pg_ctl.exe', 'psql.exe')) {
    if ([string]::IsNullOrWhiteSpace($PostgresBin) -or
        -not (Test-Path -LiteralPath (Join-Path $PostgresBin $tool))) {
        throw 'Pass -PostgresBin with a PostgreSQL installation containing initdb, pg_ctl and psql.'
    }
}

$runId = [Guid]::NewGuid().ToString('N')
$workspace = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('jvdata-mcp-postgres-' + $runId)))
$temporaryRoot = [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath([IO.Path]::GetTempPath()))
[IO.Directory]::CreateDirectory($workspace) | Out-Null
$pgData = Join-Path $workspace 'postgres'
$databaseName = 'jvdata_v1_test_mcp_' + $runId
$previousDatabaseUrl = $env:JVDATA_MCP_TEST_DATABASE_URL
$passed = $false

function Assert-Workspace {
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($workspace)) -cne $temporaryRoot -or
        [IO.Path]::GetFileName($workspace) -cne ('jvdata-mcp-postgres-' + $runId) -or
        [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($pgData)) -cne $workspace) {
        throw 'Refusing an unexpected PostgreSQL smoke workspace.'
    }
}

function Invoke-PostgresTool([string]$Name, [string[]]$ToolArguments) {
    $process = Start-Process -FilePath (Join-Path $PostgresBin $Name) -ArgumentList $ToolArguments `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $workspace "$Name.stdout.log") `
        -RedirectStandardError (Join-Path $workspace "$Name.stderr.log")
    if (-not $process.WaitForExit(60000) -or $process.ExitCode -ne 0) {
        throw "PostgreSQL smoke tool failed: $Name. Logs: $workspace"
    }
}

try {
    Assert-Workspace
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    Invoke-PostgresTool 'initdb.exe' @('-D', ('"' + $pgData + '"'), '-U', 'mcp_smoke_admin', '-A', 'trust', '-E', 'UTF8', '--locale=C')
    Invoke-PostgresTool 'pg_ctl.exe' @('-D', ('"' + $pgData + '"'), '-l', ('"' + (Join-Path $workspace 'postgres.log') + '"'),
        '-o', ('"-h 127.0.0.1 -p ' + $port + ' -F"'), '-w', 'start')

    $setup = @"
CREATE ROLE mcp_smoke_reader LOGIN;
CREATE DATABASE $databaseName;
\connect $databaseName
CREATE SCHEMA jv_data;
CREATE SCHEMA jv_mart;
CREATE TABLE jv_data.races (race_id text PRIMARY KEY);
INSERT INTO jv_data.races VALUES ('integration-race');
CREATE VIEW jv_mart.race_cards AS SELECT race_id FROM jv_data.races;
CREATE VIEW jv_mart.latest_win_place_odds AS SELECT race_id FROM jv_data.races;
GRANT USAGE ON SCHEMA jv_data, jv_mart TO mcp_smoke_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA jv_data, jv_mart TO mcp_smoke_reader;
"@
    $setupPath = Join-Path $workspace 'setup.sql'
    [IO.File]::WriteAllText($setupPath, $setup.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
    Invoke-PostgresTool 'psql.exe' @('-h', '127.0.0.1', '-p', "$port", '-U', 'mcp_smoke_admin',
        '-d', 'postgres', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', ('"' + $setupPath + '"'))
    $env:JVDATA_MCP_TEST_DATABASE_URL = "postgresql://mcp_smoke_reader@127.0.0.1:$port/$databaseName"
    Push-Location $repositoryRoot
    try {
        $resultsPath = Join-Path $workspace 'results.xml'
        uv run --no-sync pytest -q tests/test_jvdata_transport.py -k test_postgres_ --junitxml $resultsPath
        if ($LASTEXITCODE -ne 0) { throw "Windows database transport smoke failed. Logs: $workspace" }
        [xml]$results = Get-Content -LiteralPath $resultsPath -Encoding UTF8 -Raw
        $suite = $results.testsuites.testsuite
        if ([int]$suite.tests -lt 3 -or [int]$suite.skipped -ne 0) {
            throw 'The doctor, stdio and HTTP database tests must execute without skips.'
        }
    }
    finally { Pop-Location }
    $passed = $true
}
finally {
    $env:JVDATA_MCP_TEST_DATABASE_URL = $previousDatabaseUrl
    Assert-Workspace
    if (Test-Path -LiteralPath (Join-Path $pgData 'postmaster.pid')) {
        Invoke-PostgresTool 'pg_ctl.exe' @('-D', ('"' + $pgData + '"'), '-m', 'fast', '-w', 'stop')
    }
    if ($passed) {
        Assert-Workspace
        Remove-Item -LiteralPath $workspace -Recurse -Force
    }
    else { Write-Host "Private smoke results retained at $workspace" }
}
