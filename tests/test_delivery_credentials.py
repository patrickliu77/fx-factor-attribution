"""Run the credential helper against an in-memory environment and fake prompts."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


HELPER = Path(__file__).resolve().parents[1] / "ops/configure_delivery_credentials.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
EXISTING = "synthetic-existing-credential"
NEW = "synthetic-replacement-credential"


def run_helper(tmp_path, *, names, user=None, process=None, inputs=(), replace=False,
               azure=False, what_if=False):
    source = HELPER.read_text(encoding="utf-8")
    # Every environment operation is redirected before execution. Neither the
    # real process environment nor HKCU can be read or written by this copy.
    assert "[Environment]::" in source
    source = source.replace("[Environment]::", "[MockCredentialEnvironment]::")
    assert not re.search(r"\[(?:System\.)?Environment\]\s*::", source, re.I)
    candidate = tmp_path / "isolated-helper.ps1"
    candidate.write_text(source, encoding="utf-8-sig")
    config = tmp_path / "fixture.json"
    config.write_text(json.dumps({"names": names, "user": user or {}, "process": process or {},
                                  "inputs": list(inputs), "replace": replace, "azure": azure,
                                  "what_if": what_if}), encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(r'''param([string]$Fixture, [string]$Candidate)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
public static class MockCredentialEnvironment {
    public static Dictionary<string,string> User = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
    public static Dictionary<string,string> Process = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
    public static List<string> Reads = new List<string>();
    public static List<string> Writes = new List<string>();
    public static string GetEnvironmentVariable(string name, string target) {
        Reads.Add(target + ":" + name);
        var values = target == "User" ? User : Process;
        return values.ContainsKey(name) ? values[name] : null;
    }
    public static void SetEnvironmentVariable(string name, string value, string target) {
        Writes.Add(target + ":" + name);
        (target == "User" ? User : Process)[name] = value;
    }
}
'@
$fixtureData = Get-Content -LiteralPath $Fixture -Raw | ConvertFrom-Json
foreach ($entry in $fixtureData.user.PSObject.Properties) { [MockCredentialEnvironment]::User[$entry.Name] = [string]$entry.Value }
foreach ($entry in $fixtureData.process.PSObject.Properties) { [MockCredentialEnvironment]::Process[$entry.Name] = [string]$entry.Value }
$global:credentialFixtureInputs = New-Object 'System.Collections.Generic.Queue[string]'
foreach ($inputValue in $fixtureData.inputs) { $global:credentialFixtureInputs.Enqueue([string]$inputValue) }
$global:credentialFixturePromptCount = 0
$global:credentialFixtureMessages = New-Object 'System.Collections.Generic.List[string]'
function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    $global:credentialFixturePromptCount++
    if (-not $global:credentialFixtureInputs.Count) { throw 'Unexpected prompt in isolated fixture' }
    $inputValue = $global:credentialFixtureInputs.Dequeue()
    if ($AsSecureString) { return (ConvertTo-SecureString $inputValue -AsPlainText -Force) }
    return $inputValue
}
function Write-Host { param([string]$Object) $global:credentialFixtureMessages.Add($Object) }
$arguments = @{Names=@($fixtureData.names)}
if ($fixtureData.replace) { $arguments.ReplaceExisting = $true }
if ($fixtureData.azure) { $arguments.EnableAzureAudio = $true }
if ($fixtureData.what_if) { $arguments.WhatIf = $true }
$failed = $false
$errorMessage = $null
$failureLine = $null
try { & $Candidate @arguments } catch {
    $failed = $true; $errorMessage = $_.Exception.Message
    $failureLine = $_.InvocationInfo.ScriptLineNumber
}
[pscustomobject]@{
    failed=$failed; error=$errorMessage; failure_line=$failureLine; prompts=$global:credentialFixturePromptCount;
    reads=@([MockCredentialEnvironment]::Reads); writes=@([MockCredentialEnvironment]::Writes);
    user=[MockCredentialEnvironment]::User; process=[MockCredentialEnvironment]::Process;
    messages=@($global:credentialFixtureMessages)
} | ConvertTo-Json -Depth 5 -Compress
''', encoding="utf-8-sig")
    completed = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                str(harness), "-Fixture", str(config), "-Candidate", str(candidate)],
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    # No prompt or diagnostic may repeat a fake key. Real credentials are never
    # available to the harness, including in failure and WhatIf branches.
    emitted = "\n".join(result["messages"]) + str(result["error"])
    for value in [*(user or {}).values(), *(process or {}).values(), *inputs]:
        if len(value) >= 10:
            assert value not in emitted
    return result


@pytest.mark.parametrize("old_process", [{}, {"GEMINI_API_KEY": "synthetic-older-process-key"}])
def test_retained_user_value_loads_process_without_rewriting_user_or_prompting(tmp_path, old_process):
    result = run_helper(tmp_path, names=["GEMINI_API_KEY"],
                        user={"GEMINI_API_KEY": EXISTING}, process=old_process)
    assert not result["failed"] and result["prompts"] == 0
    assert result["user"] == result["process"] == {"GEMINI_API_KEY": EXISTING}
    assert result["writes"] == ["Process:GEMINI_API_KEY"]


def test_new_and_retained_settings_apply_together_after_all_prompts(tmp_path):
    result = run_helper(tmp_path, names=["GEMINI_API_KEY", "BREVO_API_KEY"],
                        user={"GEMINI_API_KEY": EXISTING}, inputs=[NEW])
    assert not result["failed"] and result["prompts"] == 1
    assert result["user"] == result["process"] == {"GEMINI_API_KEY": EXISTING, "BREVO_API_KEY": NEW}
    assert set(result["writes"]) == {"Process:GEMINI_API_KEY", "Process:BREVO_API_KEY", "User:BREVO_API_KEY"}


@pytest.mark.parametrize("invalid", ["short", "************", "invalid credential whitespace"])
def test_later_invalid_input_keeps_retained_and_new_settings_unmodified(tmp_path, invalid):
    original_process = {"GEMINI_API_KEY": "synthetic-older-process-key"}
    result = run_helper(tmp_path, names=["GEMINI_API_KEY", "BREVO_API_KEY", "FRED_API_KEY"],
                        user={"GEMINI_API_KEY": EXISTING}, process=original_process, inputs=[NEW, invalid])
    assert result["failed"] and result["prompts"] == 2
    assert result["writes"] == []
    assert result["user"] == {"GEMINI_API_KEY": EXISTING}
    assert result["process"] == original_process


def test_invalid_region_keeps_retained_key_out_of_process(tmp_path):
    result = run_helper(tmp_path, names=["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"],
                        user={"AZURE_SPEECH_KEY": EXISTING}, inputs=["not a region"])
    assert result["failed"] and result["writes"] == [] and result["process"] == {}


def test_explicit_replacement_updates_user_and_process(tmp_path):
    result = run_helper(tmp_path, names=["BREVO_API_KEY"], user={"BREVO_API_KEY": EXISTING},
                        inputs=[NEW], replace=True)
    assert not result["failed"] and result["prompts"] == 1
    assert result["user"] == result["process"] == {"BREVO_API_KEY": NEW}
    assert result["writes"] == ["User:BREVO_API_KEY", "Process:BREVO_API_KEY"]


@pytest.mark.parametrize("replace,azure", [(False, False), (True, True)])
def test_what_if_never_reads_prompts_or_writes_environment(tmp_path, replace, azure):
    user = {"BREVO_API_KEY": EXISTING}
    process = {"BREVO_API_KEY": "synthetic-older-process-key"}
    result = run_helper(tmp_path, names=["BREVO_API_KEY"], user=user, process=process,
                        replace=replace, azure=azure, what_if=True)
    assert not result["failed"] and result["prompts"] == 0
    assert result["reads"] == result["writes"] == []
    assert result["user"] == user and result["process"] == process


def test_duplicate_selected_names_load_retained_value_once(tmp_path):
    result = run_helper(tmp_path, names=["BREVO_API_KEY", "BREVO_API_KEY"], user={"BREVO_API_KEY": EXISTING})
    assert not result["failed"] and result["prompts"] == 0
    assert result["writes"] == ["Process:BREVO_API_KEY"]


def test_audio_enable_loads_required_saved_settings_without_rewriting_them(tmp_path):
    saved = {"AZURE_SPEECH_KEY": EXISTING, "AZURE_SPEECH_REGION": "eastus"}
    result = run_helper(tmp_path, names=["BREVO_API_KEY"], user=saved, inputs=[NEW], azure=True)
    assert not result["failed"] and result["prompts"] == 1
    assert result["user"] == result["process"] == {**saved, "BREVO_API_KEY": NEW, "FXDASH_AUDIO": "azure"}
    assert set(result["writes"]) == {"User:BREVO_API_KEY", "Process:BREVO_API_KEY",
                                    "User:FXDASH_AUDIO", "Process:FXDASH_AUDIO",
                                    "Process:AZURE_SPEECH_KEY", "Process:AZURE_SPEECH_REGION"}


def test_missing_audio_requirement_defers_all_user_and_process_changes(tmp_path):
    result = run_helper(tmp_path, names=["BREVO_API_KEY"], user={"AZURE_SPEECH_KEY": EXISTING},
                        inputs=[NEW], azure=True)
    assert result["failed"] and result["writes"] == []
    assert result["user"] == {"AZURE_SPEECH_KEY": EXISTING} and result["process"] == {}
